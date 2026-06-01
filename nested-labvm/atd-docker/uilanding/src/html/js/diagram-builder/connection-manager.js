/**
 * Connection Manager - Edge drawing and port assignment
 * Click source node then target node to create a connection
 */

export class ConnectionManager {
    constructor(cy, options = {}) {
        this.cy = cy;
        this.options = options;
        this.active = false;
        this.sourceNode = null;
        this.tempEdge = null;

        this.boundClickHandler = (e) => this.handleNodeClick(e);
    }

    activate() {
        this.active = true;
        this.sourceNode = null;
        this.cy.on('tap', 'node', this.boundClickHandler);

        // Visual feedback - highlight clickable nodes
        this.cy.nodes().forEach(node => {
            if (!node.data('isZone')) {
                node.addClass('hover');
            }
        });
    }

    deactivate() {
        this.active = false;
        this.sourceNode = null;
        this.cy.off('tap', 'node', this.boundClickHandler);
        this.cy.nodes().removeClass('hover');

        // Remove temp edge if exists
        if (this.tempEdge) {
            this.tempEdge.remove();
            this.tempEdge = null;
        }
    }

    handleNodeClick(e) {
        const node = e.target;

        // Skip zone parent nodes
        if (node.data('isZone')) return;

        if (!this.sourceNode) {
            // First click - select source
            this.sourceNode = node;
            node.addClass('highlighted');
            this.cy.nodes().removeClass('hover');
            // Highlight potential targets
            this.cy.nodes().forEach(n => {
                if (!n.data('isZone') && n.id() !== node.id()) {
                    n.addClass('hover');
                }
            });
        } else {
            // Second click - create edge
            const targetNode = node;

            if (targetNode.id() === this.sourceNode.id()) {
                // Clicked same node, cancel
                this.sourceNode.removeClass('highlighted');
                this.sourceNode = null;
                this.cy.nodes().forEach(n => {
                    if (!n.data('isZone')) n.addClass('hover');
                });
                return;
            }

            // Create edge (use timestamp suffix to allow parallel links)
            const edgeId = `${this.sourceNode.id()}|${targetNode.id()}:${Date.now()}`;
            const edge = this.cy.add({
                group: 'edges',
                data: {
                    id: edgeId,
                    source: this.sourceNode.id(),
                    target: targetNode.id(),
                    source_port: '',
                    target_port: '',
                },
            });

            // Cleanup
            this.sourceNode.removeClass('highlighted');
            this.cy.nodes().removeClass('hover');

            if (this.options.onConnectionCreated) {
                this.options.onConnectionCreated(edge);
            }

            // Reset for next connection
            this.sourceNode = null;
            // Re-highlight all nodes for next connection
            this.cy.nodes().forEach(n => {
                if (!n.data('isZone')) n.addClass('hover');
            });
        }
    }
}
