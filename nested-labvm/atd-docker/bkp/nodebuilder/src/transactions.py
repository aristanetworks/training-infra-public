"""
Transaction Support for Nodebuilder Service

Provides generic transaction management for multi-step resource operations
with automatic rollback on failure.

Key classes:
- ResourceTransaction: Generic transaction with forward/rollback actions
- TransactionAction: Encapsulates a single action with its rollback

Usage:
    with ResourceTransaction("create_node") as txn:
        txn.add_action(
            forward_fn=lambda: create_bridge(name),
            rollback_fn=lambda: delete_bridge(name),
            description="Create OVS bridge"
        )
        txn.add_action(
            forward_fn=lambda: define_vm(xml),
            rollback_fn=lambda: undefine_vm(name),
            description="Define VM"
        )
        txn.execute()

    # If any action fails, all previous actions are rolled back
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any

logger = logging.getLogger('nodebuilder')


@dataclass
class TransactionAction:
    """
    Represents a single action in a transaction.

    Each action has:
    - forward_fn: Function to execute the action
    - rollback_fn: Function to undo the action
    - description: Human-readable description
    - result: Stored result from forward execution
    """
    forward_fn: Callable[[], Any]
    rollback_fn: Callable[[], Any]
    description: str
    result: Any = None
    executed: bool = False


@dataclass
class ResourceTransaction:
    """
    Generic transaction for multi-resource operations with automatic rollback.

    Collects actions and executes them in order. If any action fails,
    all previously executed actions are rolled back in reverse order.

    Can be used as a context manager or explicitly via execute().
    """
    name: str
    actions: List[TransactionAction] = field(default_factory=list)
    results: List[Any] = field(default_factory=list)
    error: Optional[Exception] = None
    completed: bool = False

    def __post_init__(self):
        self.logger = logging.getLogger('nodebuilder.transaction')

    def add_action(
        self,
        forward_fn: Callable[[], Any],
        rollback_fn: Callable[[], Any],
        description: str
    ) -> None:
        """
        Add an action to the transaction.

        Actions are executed in order during execute().
        If an action fails, previously executed actions are rolled back.

        Args:
            forward_fn: Function to execute (must be callable with no args)
            rollback_fn: Function to undo the action (callable with no args)
            description: Human-readable description for logging
        """
        self.actions.append(TransactionAction(
            forward_fn=forward_fn,
            rollback_fn=rollback_fn,
            description=description
        ))

    def execute(self) -> List[Any]:
        """
        Execute all actions in order.

        If any action fails, rollback all previously executed actions
        and re-raise the exception.

        Returns:
            List of results from each action's forward function

        Raises:
            Exception: Re-raises any exception from a failed action
        """
        self.logger.info(f"Executing transaction: {self.name}")
        self.results = []

        for i, action in enumerate(self.actions):
            self.logger.debug(f"  [{i+1}/{len(self.actions)}] {action.description}")
            try:
                result = action.forward_fn()
                action.result = result
                action.executed = True
                self.results.append(result)
            except Exception as e:
                self.logger.error(
                    f"Action failed: {action.description} - {e}"
                )
                self.error = e
                self._rollback()
                raise

        self.completed = True
        self.logger.info(f"Transaction completed: {self.name}")
        return self.results

    def _rollback(self) -> None:
        """
        Rollback all executed actions in reverse order.

        Called automatically when an action fails during execute().
        """
        self.logger.info(f"Rolling back transaction: {self.name}")

        # Get executed actions in reverse order
        executed_actions = [a for a in self.actions if a.executed]

        for action in reversed(executed_actions):
            try:
                self.logger.debug(f"  Rolling back: {action.description}")
                action.rollback_fn()
            except Exception as e:
                self.logger.warning(
                    f"Rollback failed for '{action.description}': {e}"
                )
                # Continue rolling back other actions

    def __enter__(self) -> 'ResourceTransaction':
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """
        Context manager exit.

        If an exception occurred during the with block (not from execute()),
        trigger rollback. This handles cases where actions are added and
        executed manually.
        """
        if exc_type is not None and not self.completed:
            self.logger.error(
                f"Transaction {self.name} failed with exception: {exc_val}"
            )
            self._rollback()
        return False  # Re-raise exception


class NodeEditTransaction(ResourceTransaction):
    """
    Specialized transaction for editing node connections.

    Handles the pattern of:
    1. Remove old connections
    2. Add new connections

    With proper rollback if step 2 fails (re-add removed connections).
    """

    def __init__(self, node_name: str):
        super().__init__(name=f"edit_node_{node_name}")
        self.node_name = node_name
        self.removed_connections: List[Dict] = []
        self.added_connections: List[Dict] = []

    def add_remove_connection(
        self,
        connection_manager,
        connection
    ) -> None:
        """
        Add an action to remove a connection.

        Args:
            connection_manager: ConnectionManager instance
            connection: Connection object to remove
        """
        def forward():
            return connection_manager.delete_connection(
                connection,
                detach_from_source=True,
                detach_from_target=True
            )

        def rollback():
            return connection_manager.create_connection(connection)

        self.add_action(
            forward_fn=forward,
            rollback_fn=rollback,
            description=f"Remove {self.node_name}:{connection.source_port} <-> "
                       f"{connection.target_device}:{connection.target_port}"
        )

    def add_create_connection(
        self,
        connection_manager,
        connection
    ) -> None:
        """
        Add an action to create a connection.

        Args:
            connection_manager: ConnectionManager instance
            connection: Connection object to create
        """
        def forward():
            return connection_manager.create_connection(connection)

        def rollback():
            return connection_manager.delete_connection(
                connection,
                detach_from_source=True,
                detach_from_target=True
            )

        self.add_action(
            forward_fn=forward,
            rollback_fn=rollback,
            description=f"Add {self.node_name}:{connection.source_port} <-> "
                       f"{connection.target_device}:{connection.target_port}"
        )


class ClusterCreationTransaction(ResourceTransaction):
    """
    Specialized transaction for creating node clusters.

    Handles:
    1. Create all nodes in sequence
    2. Create all inter-cluster connections
    3. Create all external connections

    With full rollback on any failure.
    """

    def __init__(self, cluster_name: str):
        super().__init__(name=f"create_cluster_{cluster_name}")
        self.cluster_name = cluster_name
        self.created_nodes: List[str] = []

    def add_node_creation(
        self,
        node_name: str,
        create_fn: Callable[[], Dict],
        delete_fn: Callable[[], Dict]
    ) -> None:
        """
        Add a node creation action.

        Args:
            node_name: Name of the node to create
            create_fn: Function to create the node
            delete_fn: Function to delete the node (for rollback)
        """
        self.add_action(
            forward_fn=create_fn,
            rollback_fn=delete_fn,
            description=f"Create node: {node_name}"
        )
