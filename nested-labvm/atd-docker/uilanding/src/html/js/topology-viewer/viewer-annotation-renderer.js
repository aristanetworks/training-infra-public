/**
 * Viewer Annotation Renderer - HTML overlay annotations that track pan/zoom
 * Renders text annotations as positioned HTML divs over the Cytoscape canvas
 */

export class ViewerAnnotationRenderer {
    constructor(cy, container, annotationsData) {
        this.cy = cy;
        this.container = container;
        this.annotations = [];

        // Create overlay container
        this.overlay = document.createElement('div');
        this.overlay.className = 'annotation-overlay';
        this.container.appendChild(this.overlay);

        // Render annotations from data
        if (annotationsData && annotationsData.length) {
            annotationsData.forEach(ann => this.addAnnotation(ann));
        }

        // Track pan/zoom
        this.cy.on('pan zoom', () => this.updatePositions());
        // Initial position update after layout completes
        this.cy.on('layoutstop', () => this.updatePositions());
    }

    addAnnotation(annData) {
        const el = document.createElement('div');
        el.className = 'atl-annotation';

        const showBg = annData.background !== false;
        if (showBg) el.classList.add('has-background');

        el.textContent = annData.text || '';
        el.style.color = annData.color || '#4c5cae';
        el.style.fontSize = (annData.font_size || 12) + 'px';

        const position = annData.position || { x: 0, y: 0 };

        this.overlay.appendChild(el);
        this.annotations.push({
            el,
            modelPosition: { x: position.x, y: position.y },
        });
    }

    updatePositions() {
        const pan = this.cy.pan();
        const zoom = this.cy.zoom();

        this.annotations.forEach(ann => {
            ann.el.style.left = (ann.modelPosition.x * zoom + pan.x) + 'px';
            ann.el.style.top = (ann.modelPosition.y * zoom + pan.y) + 'px';
            ann.el.style.transform = `scale(${Math.min(zoom, 1.5)})`;
        });
    }
}
