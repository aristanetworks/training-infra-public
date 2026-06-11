#!/usr/bin/env python3
"""
Config Manager - Interactive CLI for managing Feature Flags and Announcements in Firestore.

A safe, menu-driven tool that prevents accidental deletions and provides
explicit feedback for all operations.

Usage:
    export GOOGLE_CLOUD_PROJECT=your-project-id
    python config_manager.py
"""

import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import firestore
from google.api_core import exceptions as gcp_exceptions


# =============================================================================
# CONSTANTS
# =============================================================================

FEATURES_COLLECTION = "feature-flags"
ANNOUNCEMENTS_COLLECTION = "announcements"
TOPOLOGIES_COLLECTION = "topologies"
GLOBAL_DOC = "global"
TOPOLOGIES_DOC = "topologies"

# Whitelist of allowed collections - safety guard against accidental writes to other collections
ALLOWED_COLLECTIONS = frozenset([FEATURES_COLLECTION, ANNOUNCEMENTS_COLLECTION, TOPOLOGIES_COLLECTION])

ANNOUNCEMENT_TYPES = ["info", "warning", "alert", "success"]
LINK_TYPES = ["internal", "external", "action"]
ROLLOUT_TYPES = ["full", "percentage"]
FEATURE_CATEGORIES = ["ui", "network", "integration", "experimental", "other"]


# =============================================================================
# TERMINAL COLORS AND FORMATTING
# =============================================================================

class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


def print_header(text: str):
    """Print a formatted header."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}  {text}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}\n")


def print_subheader(text: str):
    """Print a formatted subheader."""
    print(f"\n{Colors.CYAN}{Colors.BOLD}{text}{Colors.END}")
    print(f"{Colors.CYAN}{'-'*40}{Colors.END}")


def print_success(text: str):
    """Print success message."""
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")


def print_error(text: str):
    """Print error message."""
    print(f"{Colors.RED}❌ {text}{Colors.END}")


def print_warning(text: str):
    """Print warning message."""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")


def print_info(text: str):
    """Print info message."""
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.END}")


def print_item(label: str, value: Any, indent: int = 0):
    """Print a labeled item."""
    spaces = "  " * indent
    print(f"{spaces}{Colors.BOLD}{label}:{Colors.END} {value}")


# =============================================================================
# INPUT HELPERS
# =============================================================================

def get_input(prompt: str, default: Optional[str] = None, required: bool = True) -> str:
    """Get user input with optional default value."""
    if default:
        display_prompt = f"{prompt} [{default}]: "
    else:
        display_prompt = f"{prompt}: "

    while True:
        value = input(display_prompt).strip()
        if not value and default:
            return default
        if not value and required:
            print_error("This field is required.")
            continue
        if value or not required:
            return value


def get_int_input(prompt: str, default: Optional[int] = None, min_val: int = 0, max_val: int = 100) -> int:
    """Get integer input with validation."""
    while True:
        if default is not None:
            display_prompt = f"{prompt} [{default}]: "
        else:
            display_prompt = f"{prompt}: "

        value = input(display_prompt).strip()
        if not value and default is not None:
            return default

        try:
            int_val = int(value)
            if min_val <= int_val <= max_val:
                return int_val
            print_error(f"Value must be between {min_val} and {max_val}.")
        except ValueError:
            print_error("Please enter a valid number.")


def get_bool_input(prompt: str, default: bool = True) -> bool:
    """Get boolean input."""
    default_str = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{default_str}]: ").strip().lower()
        if not value:
            return default
        if value in ('y', 'yes', 'true', '1'):
            return True
        if value in ('n', 'no', 'false', '0'):
            return False
        print_error("Please enter y/n.")


def get_choice(prompt: str, options: List[str], allow_custom: bool = False) -> str:
    """Get choice from a list of options."""
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    if allow_custom:
        print(f"  {len(options) + 1}. [Enter custom value]")

    while True:
        try:
            choice = input("Select option: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
            if allow_custom and idx == len(options):
                return get_input("Enter custom value")
            print_error(f"Please select 1-{len(options) + (1 if allow_custom else 0)}.")
        except ValueError:
            print_error("Please enter a number.")


def get_date_input(prompt: str, default: Optional[str] = None) -> str:
    """Get ISO date input with validation."""
    print_info("Format: YYYY-MM-DDTHH:MM:SSZ (e.g., 2025-02-01T00:00:00Z)")
    while True:
        value = get_input(prompt, default, required=True)
        try:
            # Validate date format
            if value.endswith('Z'):
                test_str = value[:-1] + '+00:00'
            else:
                test_str = value
            datetime.fromisoformat(test_str)
            return value
        except ValueError:
            print_error("Invalid date format. Use YYYY-MM-DDTHH:MM:SSZ")


def get_list_input(prompt: str, existing: List[str] = None) -> List[str]:
    """Get a list of strings from user."""
    if existing:
        print(f"Current values: {', '.join(existing)}")
    print_info("Enter items one per line. Empty line to finish.")

    items = []
    while True:
        item = input("  > ").strip()
        if not item:
            break
        items.append(item)

    return items


def confirm_action(action: str) -> bool:
    """Confirm an action with the user."""
    print(f"\n{Colors.YELLOW}{Colors.BOLD}Confirm: {action}{Colors.END}")
    return get_bool_input("Proceed?", default=False)


def validate_identifier(name: str, label: str) -> bool:
    """
    Validate that name is safe for use as Firestore field/document key.

    Args:
        name: The identifier to validate
        label: Human-readable label for error messages (e.g., "Feature name")

    Returns:
        True if valid, False otherwise (with error message printed)
    """
    if not name:
        print_error(f"{label} cannot be empty.")
        return False
    if name.startswith('_'):
        print_error(f"{label} cannot start with underscore (reserved for metadata).")
        return False
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        print_error(f"{label} can only contain letters, numbers, hyphens, and underscores.")
        return False
    if len(name) > 100:
        print_error(f"{label} is too long (max 100 characters).")
        return False
    return True


def validate_date_range(start_date: str, end_date: str) -> bool:
    """
    Validate that start_date is before end_date and warn if end_date is in the past.

    Args:
        start_date: ISO format start date string
        end_date: ISO format end date string

    Returns:
        True if valid, False otherwise (with error message printed)
    """
    try:
        start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)

        if start_dt >= end_dt:
            print_error("Start date must be before end date.")
            return False

        if end_dt < now:
            print_warning("End date is in the past - this announcement will never be active.")
            if not get_bool_input("Continue anyway?", default=False):
                return False

        return True
    except ValueError as e:
        print_error(f"Invalid date format: {e}")
        return False


# =============================================================================
# FIRESTORE CLIENT
# =============================================================================

class ConfigManager:
    """Manager for Feature Flags and Announcements in Firestore."""

    def __init__(self):
        """Initialize Firestore client."""
        try:
            self.db = firestore.Client()
            print_success(f"Connected to Firestore project: {self.db.project}")
        except Exception as e:
            print_error(f"Failed to connect to Firestore: {e}")
            print_info("Make sure GOOGLE_CLOUD_PROJECT is set.")
            sys.exit(1)

    # -------------------------------------------------------------------------
    # Document Operations
    # -------------------------------------------------------------------------

    def get_document(self, collection: str, doc_id: str) -> Tuple[bool, Dict]:
        """
        Fetch a document from Firestore.

        Args:
            collection: Collection name (must be in ALLOWED_COLLECTIONS)
            doc_id: Document ID

        Returns:
            Tuple of (success, data):
                - (True, {...}) if document exists
                - (True, {}) if document doesn't exist (not an error)
                - (False, {}) if there was an error fetching
        """
        # Validate collection
        if collection not in ALLOWED_COLLECTIONS:
            print_error(f"Invalid collection: {collection}. Allowed: {list(ALLOWED_COLLECTIONS)}")
            return (False, {})

        try:
            doc = self.db.collection(collection).document(doc_id).get()
            if doc.exists:
                return (True, doc.to_dict())
            return (True, {})
        except gcp_exceptions.GoogleAPIError as e:
            print_error(f"Failed to fetch document: {e}")
            return (False, {})

    def update_document(self, collection: str, doc_id: str, data: Dict) -> bool:
        """Update a document in Firestore (merge mode - doesn't delete existing fields)."""
        # Validate collection
        if collection not in ALLOWED_COLLECTIONS:
            print_error(f"Invalid collection: {collection}. Allowed: {list(ALLOWED_COLLECTIONS)}")
            return False

        try:
            # Add metadata
            data["_updated_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            if "_schema_version" not in data:
                data["_schema_version"] = "2.0"

            self.db.collection(collection).document(doc_id).set(data, merge=True)
            return True
        except gcp_exceptions.GoogleAPIError as e:
            print_error(f"Failed to update document: {e}")
            return False

    def get_all_topologies(self) -> Tuple[bool, List[str]]:
        """
        Fetch all topology names from the topologies collection.

        Each document in the topologies collection represents a topology,
        with the document ID being the topology name.

        Returns:
            Tuple of (success, list of topology names)
        """
        try:
            docs = self.db.collection(TOPOLOGIES_COLLECTION).stream()
            topologies = [doc.id for doc in docs if not doc.id.startswith('_')]
            return (True, sorted(topologies))
        except gcp_exceptions.GoogleAPIError as e:
            print_error(f"Failed to fetch topologies: {e}")
            return (False, [])

    def select_topology(self, prompt: str = "Select topology") -> Optional[str]:
        """
        Display available topologies and let user select one.

        Args:
            prompt: The prompt to display

        Returns:
            Selected topology name, or None if cancelled/error
        """
        success, topologies = self.get_all_topologies()
        if not success:
            return None

        if not topologies:
            print_warning("No topologies found in the topologies collection.")
            print_info("You can enter a topology name manually.")
            topology = get_input("Topology name (e.g., training-level7-cl)")
            if not validate_identifier(topology, "Topology name"):
                return None
            return topology

        print(f"\n{Colors.BOLD}Available Topologies ({len(topologies)}):{Colors.END}")
        for i, topo in enumerate(topologies, 1):
            print(f"  {i}. {topo}")
        print(f"  {len(topologies) + 1}. [Enter custom name]")

        while True:
            try:
                choice = input(f"\n{prompt} (1-{len(topologies) + 1}): ").strip()
                if not choice:
                    return None
                idx = int(choice) - 1
                if 0 <= idx < len(topologies):
                    return topologies[idx]
                if idx == len(topologies):
                    topology = get_input("Topology name")
                    if not validate_identifier(topology, "Topology name"):
                        return None
                    return topology
                print_error(f"Please select 1-{len(topologies) + 1}.")
            except ValueError:
                print_error("Please enter a number.")

    # -------------------------------------------------------------------------
    # Feature Flags Operations
    # -------------------------------------------------------------------------

    def list_global_features(self):
        """List all global features."""
        print_subheader("Global Feature Flags")

        success, data = self.get_document(FEATURES_COLLECTION, GLOBAL_DOC)
        if not success:
            return
        if not data:
            print_warning("No global features document found.")
            return

        enabled = data.get("enabled_features", [])
        definitions = data.get("feature_definitions", {})

        print(f"\n{Colors.BOLD}Enabled Features ({len(enabled)}):{Colors.END}")
        for feat in sorted(enabled):
            status = f"{Colors.GREEN}●{Colors.END}"
            print(f"  {status} {feat}")

        if definitions:
            print(f"\n{Colors.BOLD}Feature Definitions ({len(definitions)}):{Colors.END}")
            for name, defn in sorted(definitions.items()):
                is_enabled = name in enabled
                status = f"{Colors.GREEN}●{Colors.END}" if is_enabled else f"{Colors.RED}○{Colors.END}"
                print(f"\n  {status} {Colors.BOLD}{name}{Colors.END}")
                print(f"      Description: {defn.get('description', 'N/A')}")
                print(f"      Category: {defn.get('category', 'N/A')}")
                print(f"      Owner: {defn.get('owner', 'N/A')}")
                rollout = defn.get('rollout', {})
                if rollout:
                    print(f"      Rollout: {rollout.get('type', 'full')} ({rollout.get('percentage', 100)}%)")
                deps = defn.get('dependencies', [])
                if deps:
                    print(f"      Dependencies: {', '.join(deps)}")

    def list_topology_features(self):
        """List features by topology."""
        print_subheader("Topology Feature Flags")

        success, data = self.get_document(FEATURES_COLLECTION, TOPOLOGIES_DOC)
        if not success:
            return
        if not data:
            print_warning("No topology features document found.")
            return

        # Filter out metadata fields
        topologies = {k: v for k, v in data.items() if not k.startswith('_')}

        if not topologies:
            print_warning("No topologies configured.")
            return

        for topo, config in sorted(topologies.items()):
            print(f"\n{Colors.BOLD}{topo}{Colors.END}")
            if isinstance(config, dict):
                features = config.get('enabled_features', [])
                overrides = config.get('overrides', {})
            else:
                # Legacy format - just a list
                features = config if isinstance(config, list) else []
                overrides = {}

            if features:
                for feat in features:
                    print(f"  {Colors.GREEN}●{Colors.END} {feat}")
            else:
                print(f"  {Colors.YELLOW}(no features enabled){Colors.END}")

            if overrides:
                print(f"  {Colors.CYAN}Overrides:{Colors.END}")
                for feat, override in overrides.items():
                    print(f"    {feat}: {json.dumps(override)}")

    def add_feature_definition(self):
        """Add a new feature definition."""
        print_subheader("Add Feature Definition")

        # Get current data
        success, data = self.get_document(FEATURES_COLLECTION, GLOBAL_DOC)
        if not success:
            return
        definitions = data.get("feature_definitions", {})

        # Get feature details
        name = get_input("Feature name (e.g., dark_mode)")
        if not validate_identifier(name, "Feature name"):
            return
        if name in definitions:
            print_warning(f"Feature '{name}' already exists. Use 'Edit' to modify.")
            return

        description = get_input("Description")
        category = get_choice("Category", FEATURE_CATEGORIES, allow_custom=True)
        owner = get_input("Owner (team or person)", default="platform-team")

        # Rollout config
        print_subheader("Rollout Configuration")
        rollout_type = get_choice("Rollout type", ROLLOUT_TYPES)
        rollout = {"type": rollout_type, "percentage": 100}

        if rollout_type == "percentage":
            rollout["percentage"] = get_int_input("Percentage", default=100, min_val=0, max_val=100)
            rollout["hash_key"] = get_input("Hash key for bucketing", default="instance_id")

        # Dependencies
        print_subheader("Dependencies")
        existing_features = list(definitions.keys())
        if existing_features:
            print(f"Available features: {', '.join(existing_features)}")
        dependencies = get_list_input("Enter dependencies (feature names)")

        # Validate dependencies exist
        for dep in dependencies:
            if dep not in definitions and dep not in data.get("enabled_features", []):
                print_warning(f"Dependency '{dep}' is not defined. It will still be added.")

        # Build feature definition
        feature_def = {
            "description": description,
            "category": category,
            "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "owner": owner,
            "dependencies": dependencies,
            "rollout": rollout
        }

        # Preview
        print_subheader("Preview")
        print(json.dumps({name: feature_def}, indent=2))

        # Also ask if they want to enable it
        enable_now = get_bool_input("Enable this feature globally now?", default=False)

        if confirm_action(f"Add feature definition '{name}'"):
            definitions[name] = feature_def

            update_data = {"feature_definitions": definitions}
            if enable_now:
                enabled = data.get("enabled_features", [])
                if name not in enabled:
                    enabled.append(name)
                update_data["enabled_features"] = enabled

            if self.update_document(FEATURES_COLLECTION, GLOBAL_DOC, update_data):
                print_success(f"Feature '{name}' added successfully!")
                if enable_now:
                    print_success(f"Feature '{name}' is now enabled globally.")
            else:
                print_error("Failed to add feature.")

    def toggle_global_feature(self):
        """Enable or disable a global feature."""
        print_subheader("Toggle Global Feature")

        success, data = self.get_document(FEATURES_COLLECTION, GLOBAL_DOC)
        if not success:
            return
        enabled = data.get("enabled_features", [])
        definitions = data.get("feature_definitions", {})

        # Show current state
        all_features = sorted(set(enabled) | set(definitions.keys()))
        if not all_features:
            print_warning("No features defined. Add a feature definition first.")
            return

        print("\nCurrent feature states:")
        for i, feat in enumerate(all_features, 1):
            is_enabled = feat in enabled
            status = f"{Colors.GREEN}ENABLED{Colors.END}" if is_enabled else f"{Colors.RED}DISABLED{Colors.END}"
            print(f"  {i}. {feat} [{status}]")

        # Select feature
        try:
            choice = int(get_input("Select feature number")) - 1
            if 0 <= choice < len(all_features):
                feature = all_features[choice]
            else:
                print_error("Invalid selection.")
                return
        except ValueError:
            print_error("Please enter a number.")
            return

        is_enabled = feature in enabled
        action = "disable" if is_enabled else "enable"

        if confirm_action(f"{action.capitalize()} feature '{feature}'"):
            if is_enabled:
                enabled.remove(feature)
            else:
                enabled.append(feature)

            if self.update_document(FEATURES_COLLECTION, GLOBAL_DOC, {"enabled_features": enabled}):
                print_success(f"Feature '{feature}' is now {action}d.")
            else:
                print_error(f"Failed to {action} feature.")

    def manage_topology_features(self):
        """Manage features for a specific topology."""
        print_subheader("Manage Topology Features")

        # Get current topology data
        success, topo_data = self.get_document(FEATURES_COLLECTION, TOPOLOGIES_DOC)
        if not success:
            return
        success, global_data = self.get_document(FEATURES_COLLECTION, GLOBAL_DOC)
        if not success:
            return

        # Select topology from topologies collection
        topology = self.select_topology("Select topology to manage")
        if not topology:
            return

        # Get current config for this topology
        current_config = topo_data.get(topology, {})
        if isinstance(current_config, list):
            # Convert legacy format
            current_config = {"enabled_features": current_config, "overrides": {}}

        current_features = current_config.get("enabled_features", [])

        # Show available features from global definitions
        all_features = list(global_data.get("feature_definitions", {}).keys())
        all_features.extend(global_data.get("enabled_features", []))
        all_features = sorted(set(all_features))

        if not all_features:
            print_warning("No global features defined. Add feature definitions first.")
            return

        print(f"\nFeatures for topology '{topology}':")
        for i, feat in enumerate(all_features, 1):
            is_enabled = feat in current_features
            status = f"{Colors.GREEN}●{Colors.END}" if is_enabled else f"{Colors.RED}○{Colors.END}"
            print(f"  {i}. {status} {feat}")

        print("\nOptions:")
        print("  a. Add features to this topology")
        print("  r. Remove features from this topology")
        print("  q. Back to menu")

        choice = get_input("Select option").lower()

        if choice == 'a':
            print("\nEnter feature numbers to add (comma-separated, e.g., 1,3,5):")
            selections = get_input("Features to add")
            try:
                indices = [int(x.strip()) - 1 for x in selections.split(',')]
                features_to_add = [all_features[i] for i in indices if 0 <= i < len(all_features)]

                for feat in features_to_add:
                    if feat not in current_features:
                        current_features.append(feat)
                        print_info(f"Adding: {feat}")
                    else:
                        print_warning(f"Already enabled: {feat}")

            except (ValueError, IndexError):
                print_error("Invalid selection.")
                return

        elif choice == 'r':
            if not current_features:
                print_warning("No features to remove.")
                return

            print("\nEnter feature numbers to remove (comma-separated):")
            selections = get_input("Features to remove")
            try:
                indices = [int(x.strip()) - 1 for x in selections.split(',')]
                features_to_remove = [all_features[i] for i in indices if 0 <= i < len(all_features)]

                for feat in features_to_remove:
                    if feat in current_features:
                        current_features.remove(feat)
                        print_info(f"Removing: {feat}")
                    else:
                        print_warning(f"Not enabled: {feat}")

            except (ValueError, IndexError):
                print_error("Invalid selection.")
                return
        else:
            return

        # Update config
        current_config["enabled_features"] = current_features

        # Preview
        print_subheader("Preview")
        print(json.dumps({topology: current_config}, indent=2))

        if confirm_action(f"Update features for '{topology}'"):
            topo_data[topology] = current_config
            if self.update_document(FEATURES_COLLECTION, TOPOLOGIES_DOC, topo_data):
                print_success(f"Topology '{topology}' updated successfully!")
            else:
                print_error("Failed to update topology.")

    def edit_feature_definition(self):
        """Edit an existing feature definition."""
        print_subheader("Edit Feature Definition")

        success, data = self.get_document(FEATURES_COLLECTION, GLOBAL_DOC)
        if not success:
            return
        definitions = data.get("feature_definitions", {})

        if not definitions:
            print_warning("No feature definitions found. Add one first.")
            return

        # List features
        features = sorted(definitions.keys())
        print("\nFeature definitions:")
        for i, feat in enumerate(features, 1):
            print(f"  {i}. {feat}")

        try:
            choice = int(get_input("Select feature to edit")) - 1
            if 0 <= choice < len(features):
                feature = features[choice]
            else:
                print_error("Invalid selection.")
                return
        except ValueError:
            print_error("Please enter a number.")
            return

        current = definitions[feature]

        print(f"\nEditing: {Colors.BOLD}{feature}{Colors.END}")
        print_info("Press Enter to keep current value.")

        # Edit fields
        description = get_input("Description", current.get("description", ""))
        category = get_input("Category", current.get("category", "other"))
        owner = get_input("Owner", current.get("owner", ""))

        # Rollout
        current_rollout = current.get("rollout", {"type": "full", "percentage": 100})
        print(f"\nCurrent rollout: {current_rollout}")
        if get_bool_input("Edit rollout?", default=False):
            rollout_type = get_choice("Rollout type", ROLLOUT_TYPES)
            rollout = {"type": rollout_type}
            if rollout_type == "percentage":
                rollout["percentage"] = get_int_input("Percentage", current_rollout.get("percentage", 100))
                rollout["hash_key"] = get_input("Hash key", current_rollout.get("hash_key", "instance_id"))
            else:
                rollout["percentage"] = 100
        else:
            rollout = current_rollout

        # Dependencies
        current_deps = current.get("dependencies", [])
        print(f"\nCurrent dependencies: {current_deps}")
        if get_bool_input("Edit dependencies?", default=False):
            dependencies = get_list_input("Enter dependencies", current_deps)
        else:
            dependencies = current_deps

        # Update definition
        definitions[feature] = {
            "description": description,
            "category": category,
            "created_at": current.get("created_at", datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')),
            "updated_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "owner": owner,
            "dependencies": dependencies,
            "rollout": rollout
        }

        # Preview
        print_subheader("Preview")
        print(json.dumps({feature: definitions[feature]}, indent=2))

        if confirm_action(f"Update feature '{feature}'"):
            if self.update_document(FEATURES_COLLECTION, GLOBAL_DOC, {"feature_definitions": definitions}):
                print_success(f"Feature '{feature}' updated successfully!")
            else:
                print_error("Failed to update feature.")

    # -------------------------------------------------------------------------
    # Announcement Operations
    # -------------------------------------------------------------------------

    def list_global_announcements(self):
        """List all global announcements."""
        print_subheader("Global Announcements")

        success, data = self.get_document(ANNOUNCEMENTS_COLLECTION, GLOBAL_DOC)
        if not success:
            return
        announcements = data.get("announcements", [])

        if not announcements:
            print_warning("No global announcements found.")
            return

        now = datetime.now(timezone.utc)

        for ann in announcements:
            self._print_announcement(ann, now)

    def list_topology_announcements(self):
        """List announcements by topology."""
        print_subheader("Topology Announcements")

        success, data = self.get_document(ANNOUNCEMENTS_COLLECTION, TOPOLOGIES_DOC)
        if not success:
            return
        if not data:
            print_warning("No topology announcements document found.")
            return

        topologies = {k: v for k, v in data.items() if not k.startswith('_')}
        now = datetime.now(timezone.utc)

        for topo, announcements in sorted(topologies.items()):
            print(f"\n{Colors.BOLD}{Colors.UNDERLINE}{topo}{Colors.END}")
            if announcements:
                for ann in announcements:
                    self._print_announcement(ann, now, indent=1)
            else:
                print(f"  {Colors.YELLOW}(no announcements){Colors.END}")

    def _print_announcement(self, ann: Dict, now: datetime, indent: int = 0):
        """Print a single announcement."""
        spaces = "  " * indent
        ann_type = ann.get("type", "info")
        type_colors = {
            "info": Colors.BLUE,
            "warning": Colors.YELLOW,
            "alert": Colors.RED,
            "success": Colors.GREEN
        }
        color = type_colors.get(ann_type, Colors.BLUE)

        # Check if active
        try:
            start = datetime.fromisoformat(ann.get("start_date", "").replace('Z', '+00:00'))
            end = datetime.fromisoformat(ann.get("end_date", "").replace('Z', '+00:00'))
            is_active = start <= now <= end
            status = f"{Colors.GREEN}ACTIVE{Colors.END}" if is_active else f"{Colors.RED}INACTIVE{Colors.END}"
        except (ValueError, TypeError):
            status = f"{Colors.YELLOW}UNKNOWN{Colors.END}"

        print(f"\n{spaces}{color}[{ann_type.upper()}]{Colors.END} {Colors.BOLD}{ann.get('title', 'Untitled')}{Colors.END} [{status}]")
        print(f"{spaces}  ID: {ann.get('id', 'N/A')}")
        print(f"{spaces}  Message: {ann.get('message', 'N/A')[:80]}...")
        print(f"{spaces}  Priority: {ann.get('priority', 50)} | Dismissible: {ann.get('dismissible', True)}")
        print(f"{spaces}  Active: {ann.get('start_date', 'N/A')} → {ann.get('end_date', 'N/A')}")

        tags = ann.get("tags", [])
        if tags:
            print(f"{spaces}  Tags: {', '.join(tags)}")

        links = ann.get("links", [])
        if links:
            print(f"{spaces}  Links:")
            for link in links:
                print(f"{spaces}    - [{link.get('type', 'external')}] {link.get('label', 'Link')}: {link.get('url', 'N/A')}")

    def add_announcement(self, topology: Optional[str] = None):
        """Add a new announcement."""
        if topology:
            print_subheader(f"Add Announcement to '{topology}'")
        else:
            print_subheader("Add Global Announcement")

        # Generate unique ID (12 hex chars for better collision resistance)
        default_id = f"ann-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:12]}"

        # Get announcement details
        ann_id = get_input("Announcement ID", default=default_id)
        title = get_input("Title")
        message = get_input("Message")
        ann_type = get_choice("Type", ANNOUNCEMENT_TYPES)
        priority = get_int_input("Priority (0-100, higher = more important)", default=50, min_val=0, max_val=100)
        dismissible = get_bool_input("Dismissible?", default=True)

        # Dates
        print_subheader("Active Period")
        default_start = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        start_date = get_date_input("Start date", default=default_start)

        # Dynamic default end date: end of current year
        default_end = datetime.now(timezone.utc).replace(
            month=12, day=31, hour=23, minute=59, second=59, microsecond=0
        ).isoformat().replace('+00:00', 'Z')
        end_date = get_date_input("End date", default=default_end)

        # Validate date range
        if not validate_date_range(start_date, end_date):
            return

        # Tags
        print_subheader("Tags (optional)")
        tags = get_list_input("Enter tags")

        # Links
        print_subheader("Links (optional)")
        links = []
        while True:
            if not get_bool_input("Add a link?", default=len(links) == 0):
                break
            link_label = get_input("Link label (e.g., 'Learn More')")
            link_url = get_input("Link URL")
            link_type = get_choice("Link type", LINK_TYPES)
            links.append({"label": link_label, "url": link_url, "type": link_type})
            print_success(f"Link added: {link_label}")

        # Build announcement
        announcement = {
            "id": ann_id,
            "title": title,
            "message": message,
            "type": ann_type,
            "priority": priority,
            "dismissible": dismissible,
            "start_date": start_date,
            "end_date": end_date,
            "tags": tags,
            "links": links
        }

        # Preview
        print_subheader("Preview")
        print(json.dumps(announcement, indent=2))

        if not confirm_action("Add this announcement"):
            print_info("Cancelled.")
            return

        # Add to Firestore
        if topology:
            success, data = self.get_document(ANNOUNCEMENTS_COLLECTION, TOPOLOGIES_DOC)
            if not success:
                return
            topo_announcements = data.get(topology, [])

            # Check for duplicate ID
            if any(a.get("id") == ann_id for a in topo_announcements):
                print_error(f"Announcement with ID '{ann_id}' already exists in this topology.")
                return

            topo_announcements.append(announcement)
            data[topology] = topo_announcements

            if self.update_document(ANNOUNCEMENTS_COLLECTION, TOPOLOGIES_DOC, data):
                print_success(f"Announcement added to topology '{topology}'!")
            else:
                print_error("Failed to add announcement.")
        else:
            success, data = self.get_document(ANNOUNCEMENTS_COLLECTION, GLOBAL_DOC)
            if not success:
                return
            announcements = data.get("announcements", [])

            # Check for duplicate ID
            if any(a.get("id") == ann_id for a in announcements):
                print_error(f"Announcement with ID '{ann_id}' already exists.")
                return

            announcements.append(announcement)

            if self.update_document(ANNOUNCEMENTS_COLLECTION, GLOBAL_DOC, {"announcements": announcements}):
                print_success("Global announcement added!")
            else:
                print_error("Failed to add announcement.")

    def edit_announcement(self, topology: Optional[str] = None):
        """Edit an existing announcement."""
        if topology:
            print_subheader(f"Edit Announcement in '{topology}'")
            success, data = self.get_document(ANNOUNCEMENTS_COLLECTION, TOPOLOGIES_DOC)
            if not success:
                return
            announcements = data.get(topology, [])
        else:
            print_subheader("Edit Global Announcement")
            success, data = self.get_document(ANNOUNCEMENTS_COLLECTION, GLOBAL_DOC)
            if not success:
                return
            announcements = data.get("announcements", [])

        if not announcements:
            print_warning("No announcements found.")
            return

        # List announcements
        print("\nAnnouncements:")
        for i, ann in enumerate(announcements, 1):
            print(f"  {i}. [{ann.get('type', 'info')}] {ann.get('title', 'Untitled')} (ID: {ann.get('id', 'N/A')})")

        try:
            choice = int(get_input("Select announcement to edit")) - 1
            if not (0 <= choice < len(announcements)):
                print_error("Invalid selection.")
                return
        except ValueError:
            print_error("Please enter a number.")
            return

        current = announcements[choice]
        print(f"\nEditing: {Colors.BOLD}{current.get('title')}{Colors.END}")
        print_info("Press Enter to keep current value.")

        # Edit fields
        title = get_input("Title", current.get("title"))
        message = get_input("Message", current.get("message"))

        # Type selection with current value shown
        current_type = current.get("type", "info")
        print(f"\nCurrent type: {current_type}")
        if get_bool_input("Change type?", default=False):
            ann_type = get_choice("Type", ANNOUNCEMENT_TYPES)
        else:
            ann_type = current_type

        priority = get_int_input("Priority", current.get("priority", 50))
        dismissible = get_bool_input("Dismissible?", current.get("dismissible", True))

        # Dates
        start_date = get_date_input("Start date", current.get("start_date"))
        end_date = get_date_input("End date", current.get("end_date"))

        # Validate date range
        if not validate_date_range(start_date, end_date):
            return

        # Tags
        current_tags = current.get("tags", [])
        print(f"\nCurrent tags: {current_tags}")
        if get_bool_input("Edit tags?", default=False):
            tags = get_list_input("Enter tags", current_tags)
        else:
            tags = current_tags

        # Links
        current_links = current.get("links", [])
        print(f"\nCurrent links: {len(current_links)} link(s)")
        for link in current_links:
            print(f"  - {link.get('label')}: {link.get('url')}")

        if get_bool_input("Edit links?", default=False):
            links = []
            while True:
                if not get_bool_input("Add a link?", default=len(links) < len(current_links)):
                    break
                # Pre-fill from existing if available
                default_label = current_links[len(links)].get("label", "") if len(links) < len(current_links) else ""
                default_url = current_links[len(links)].get("url", "") if len(links) < len(current_links) else ""
                default_type = current_links[len(links)].get("type", "external") if len(links) < len(current_links) else "external"

                link_label = get_input("Link label", default_label)
                link_url = get_input("Link URL", default_url)
                print(f"Current link type: {default_type}")
                if get_bool_input("Change link type?", default=False):
                    link_type = get_choice("Link type", LINK_TYPES)
                else:
                    link_type = default_type
                links.append({"label": link_label, "url": link_url, "type": link_type})
        else:
            links = current_links

        # Update announcement
        updated = {
            "id": current.get("id"),  # Keep original ID
            "title": title,
            "message": message,
            "type": ann_type,
            "priority": priority,
            "dismissible": dismissible,
            "start_date": start_date,
            "end_date": end_date,
            "tags": tags,
            "links": links
        }

        # Preview
        print_subheader("Preview")
        print(json.dumps(updated, indent=2))

        if not confirm_action("Save changes"):
            print_info("Cancelled.")
            return

        # Save
        announcements[choice] = updated

        if topology:
            data[topology] = announcements
            if self.update_document(ANNOUNCEMENTS_COLLECTION, TOPOLOGIES_DOC, data):
                print_success("Announcement updated!")
            else:
                print_error("Failed to update announcement.")
        else:
            if self.update_document(ANNOUNCEMENTS_COLLECTION, GLOBAL_DOC, {"announcements": announcements}):
                print_success("Announcement updated!")
            else:
                print_error("Failed to update announcement.")

    def add_topology_announcement(self):
        """Add an announcement to a specific topology."""
        print_subheader("Add Topology Announcement")

        # Select topology from topologies collection
        topology = self.select_topology("Select topology for announcement")
        if not topology:
            return
        self.add_announcement(topology=topology)

    def edit_topology_announcement(self):
        """Edit an announcement in a specific topology."""
        print_subheader("Edit Topology Announcement")

        # Get topologies that have announcements
        success, data = self.get_document(ANNOUNCEMENTS_COLLECTION, TOPOLOGIES_DOC)
        if not success:
            return

        # Filter to topologies with existing announcements
        topos_with_announcements = {
            k: v for k, v in data.items()
            if not k.startswith('_') and v and isinstance(v, list) and len(v) > 0
        }

        if not topos_with_announcements:
            print_warning("No topologies with announcements found.")
            print_info("Use 'Add topology announcement' to create one first.")
            return

        print(f"\n{Colors.BOLD}Topologies with Announcements:{Colors.END}")
        sorted_topos = sorted(topos_with_announcements.keys())
        for i, t in enumerate(sorted_topos, 1):
            count = len(topos_with_announcements[t])
            print(f"  {i}. {t} ({count} announcement(s))")

        while True:
            try:
                choice = input(f"\nSelect topology (1-{len(sorted_topos)}): ").strip()
                if not choice:
                    return
                idx = int(choice) - 1
                if 0 <= idx < len(sorted_topos):
                    topology = sorted_topos[idx]
                    break
                print_error(f"Please select 1-{len(sorted_topos)}.")
            except ValueError:
                print_error("Please enter a number.")

        self.edit_announcement(topology=topology)


    def list_topologies(self):
        """List all available topologies from the topologies collection."""
        print_subheader("Available Topologies")

        success, topologies = self.get_all_topologies()
        if not success:
            return

        if not topologies:
            print_warning("No topologies found in the topologies collection.")
            return

        print(f"\n{Colors.BOLD}Topologies ({len(topologies)}):{Colors.END}")
        for topo in topologies:
            print(f"  {Colors.GREEN}●{Colors.END} {topo}")


# =============================================================================
# MAIN MENU
# =============================================================================

def main_menu(manager: ConfigManager):
    """Display main menu and handle user input."""
    while True:
        print_header("Config Manager - Feature Flags & Announcements")

        print(f"{Colors.BOLD}Feature Flags{Colors.END}")
        print("  1. List global features")
        print("  2. List topology features")
        print("  3. Add feature definition")
        print("  4. Edit feature definition")
        print("  5. Toggle global feature (enable/disable)")
        print("  6. Manage topology features")

        print(f"\n{Colors.BOLD}Announcements{Colors.END}")
        print("  7. List global announcements")
        print("  8. List topology announcements")
        print("  9. Add global announcement")
        print("  10. Add topology announcement")
        print("  11. Edit global announcement")
        print("  12. Edit topology announcement")

        print(f"\n{Colors.BOLD}Topologies{Colors.END}")
        print("  13. List all topologies")

        print(f"\n{Colors.BOLD}General{Colors.END}")
        print("  q. Quit")

        choice = input(f"\n{Colors.CYAN}Select option: {Colors.END}").strip().lower()

        if choice == '1':
            manager.list_global_features()
        elif choice == '2':
            manager.list_topology_features()
        elif choice == '3':
            manager.add_feature_definition()
        elif choice == '4':
            manager.edit_feature_definition()
        elif choice == '5':
            manager.toggle_global_feature()
        elif choice == '6':
            manager.manage_topology_features()
        elif choice == '7':
            manager.list_global_announcements()
        elif choice == '8':
            manager.list_topology_announcements()
        elif choice == '9':
            manager.add_announcement()
        elif choice == '10':
            manager.add_topology_announcement()
        elif choice == '11':
            manager.edit_announcement()
        elif choice == '12':
            manager.edit_topology_announcement()
        elif choice == '13':
            manager.list_topologies()
        elif choice == 'q':
            print_info("Goodbye!")
            break
        else:
            print_error("Invalid option. Please try again.")

        input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")


def main():
    """Main entry point."""
    print_header("Config Manager v1.2")
    print_info("Connecting to Firestore...")

    manager = ConfigManager()
    main_menu(manager)


if __name__ == "__main__":
    main()
