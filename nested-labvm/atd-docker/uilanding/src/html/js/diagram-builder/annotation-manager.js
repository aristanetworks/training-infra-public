/**
 * Annotation Manager - Text overlay creation and editing
 * Renders HTML overlay divs that track Cytoscape pan/zoom
 */

export class AnnotationManager {
    constructor(cy, container, options = {}) {
        this.cy = cy;
        this.container = container;
        this.options = options;
        this.annotations = [];
        this.createMode = false;
        this.selectedIndex = -1;

        // Track pan/zoom for annotation positioning
        this.cy.on('pan zoom', () => this.updatePositions());

        // Click handler for create mode
        this.boundCanvasClick = (e) => this.handleCanvasClick(e);
    }

    activateCreateMode() {
        this.createMode = true;
        this.cy.userPanningEnabled(false);
        this.container.parentElement.addEventListener('click', this.boundCanvasClick);
    }

    deactivateCreateMode() {
        this.createMode = false;
        this.cy.userPanningEnabled(true);
        this.container.parentElement.removeEventListener('click', this.boundCanvasClick);
    }

    handleCanvasClick(e) {
        if (!this.createMode) return;

        // Don't create annotation if clicking on a node or the toolbar
        if (e.target.closest('.builder-toolbar') || e.target.closest('.builder-panel')) return;

        const rect = this.container.getBoundingClientRect();
        const pan = this.cy.pan();
        const zoom = this.cy.zoom();

        // Convert screen position to model position
        const modelPosition = {
            x: Math.round((e.clientX - rect.left - pan.x) / zoom),
            y: Math.round((e.clientY - rect.top - pan.y) / zoom),
        };

        this.createAnnotation('Text', modelPosition);

        if (this.options.onChange) {
            this.options.onChange();
        }

        // Exit create mode after placing
        this.deactivateCreateMode();
        if (this.options.onSelect) {
            const ann = this.annotations[this.annotations.length - 1];
            this.options.onSelect({ ...ann, index: this.annotations.length - 1 });
        }
    }

    createAnnotation(text, modelPosition, opts = {}) {
        const annotation = {
            text: text,
            modelPosition: { ...modelPosition },
            color: opts.color || '#4c5cae',
            fontSize: opts.fontSize || 12,
            background: opts.background !== false,
            el: null,
        };

        // Create DOM element
        const el = document.createElement('div');
        el.className = 'atl-annotation';
        if (annotation.background) el.classList.add('has-background');
        el.textContent = annotation.text;
        el.style.color = annotation.color;
        el.style.fontSize = annotation.fontSize + 'px';

        // Click to select
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            const idx = this.annotations.indexOf(annotation);
            this.selectAnnotation(idx);
        });

        // Drag support
        this.makeDraggable(el, annotation);

        annotation.el = el;
        this.container.appendChild(el);
        this.annotations.push(annotation);

        this.updatePositions();
        return annotation;
    }

    makeDraggable(el, annotation) {
        let startX, startY, startModelX, startModelY;
        let isDragging = false;

        const onMouseDown = (e) => {
            if (e.button !== 0) return;
            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;
            startModelX = annotation.modelPosition.x;
            startModelY = annotation.modelPosition.y;
            e.preventDefault();

            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
        };

        const onMouseMove = (e) => {
            if (!isDragging) return;
            const zoom = this.cy.zoom();
            const dx = (e.clientX - startX) / zoom;
            const dy = (e.clientY - startY) / zoom;
            annotation.modelPosition.x = Math.round(startModelX + dx);
            annotation.modelPosition.y = Math.round(startModelY + dy);
            this.updatePositions();
        };

        const onMouseUp = () => {
            isDragging = false;
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            if (this.options.onChange) {
                this.options.onChange();
            }
        };

        el.addEventListener('mousedown', onMouseDown);
    }

    selectAnnotation(index) {
        // Deselect previous
        this.annotations.forEach(a => {
            if (a.el) a.el.classList.remove('selected');
        });

        this.selectedIndex = index;
        if (index >= 0 && index < this.annotations.length) {
            this.annotations[index].el.classList.add('selected');
            // Deselect cytoscape elements
            this.cy.elements(':selected').unselect();

            if (this.options.onSelect) {
                const ann = this.annotations[index];
                this.options.onSelect({
                    text: ann.text,
                    color: ann.color,
                    fontSize: ann.fontSize,
                    background: ann.background,
                    index: index,
                });
            }
        }
    }

    updateAnnotation(index, changes) {
        if (index < 0 || index >= this.annotations.length) return;
        const ann = this.annotations[index];

        if (changes.text !== undefined) {
            ann.text = changes.text;
            ann.el.textContent = changes.text;
        }
        if (changes.color !== undefined) {
            ann.color = changes.color;
            ann.el.style.color = changes.color;
        }
        if (changes.fontSize !== undefined) {
            ann.fontSize = parseInt(changes.fontSize);
            ann.el.style.fontSize = ann.fontSize + 'px';
        }
        if (changes.background !== undefined) {
            ann.background = changes.background;
            ann.el.classList.toggle('has-background', changes.background);
        }

        if (this.options.onChange) {
            this.options.onChange();
        }
    }

    updatePositions() {
        const pan = this.cy.pan();
        const zoom = this.cy.zoom();

        this.annotations.forEach(a => {
            if (!a.el) return;
            a.el.style.left = (a.modelPosition.x * zoom + pan.x) + 'px';
            a.el.style.top = (a.modelPosition.y * zoom + pan.y) + 'px';
            a.el.style.transform = `scale(${Math.min(zoom, 1.5)})`;
        });
    }

    getAnnotations() {
        return this.annotations.map(a => ({
            text: a.text,
            position: { x: a.modelPosition.x, y: a.modelPosition.y },
            color: a.color,
            font_size: a.fontSize,
            background: a.background,
        }));
    }

    removeAnnotation(index) {
        if (index < 0 || index >= this.annotations.length) return;
        const ann = this.annotations[index];
        if (ann.el) ann.el.remove();
        this.annotations.splice(index, 1);

        if (this.options.onChange) {
            this.options.onChange();
        }
    }

    clear() {
        this.annotations.forEach(a => {
            if (a.el) a.el.remove();
        });
        this.annotations = [];
        this.selectedIndex = -1;
    }
}
