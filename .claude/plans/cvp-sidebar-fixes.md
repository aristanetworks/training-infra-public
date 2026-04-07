# CVP Sidebar Link Fixes

Plan for addressing code review findings on commit `5a757077` ("Replace dual CVP sidebar elements with single stateful link") on branch `improvment/update-cvp-link`.

## Context

The commit replaced two separate `<li>` elements (`cvpLoading`/`cvpLoaded`) with a single stateful `<li id="cvpLink">` that toggles a `cvp-disabled` CSS class. The code reviewer found 2 critical issues, 1 major concern, and several minor items.

## Files to modify

- `nested-labvm/atd-docker/uilanding/src/html/js/atd-ws.js`
- `nested-labvm/atd-docker/uilanding/src/html/css/home.css`
- `nested-labvm/atd-docker/uilanding/src/html/index.html`

---

## Step 1: Flatten cvpLink guard logic in atd-ws.js (Critical)

**Problem:** `cvpLink` toggling is nested inside `if (elements.cvpStatus)` at lines ~183-193 and ~239-248. If a topology doesn't render `#cvpStatus`, the sidebar link stays disabled forever.

**Fix:** Move `cvpLink` manipulation out of the `cvpStatus` conditional so both guards run independently:

```javascript
// CVP NOT UP branch (~line 182)
if (elements.labBtn) {
    elements.labBtn.disabled = true
}
if (elements.cvpStatus) {
    elements.cvpStatus.textContent = "CVP is currently starting..."
}
if (elements.cvpLink) {
    elements.cvpLink.classList.add('cvp-disabled');
    if (elements.cvpLinkAnchor) {
        elements.cvpLinkAnchor.removeAttribute('href');
        elements.cvpLinkAnchor.removeAttribute('target');
    }
}

// CVP UP branch (~line 237)
if (elements.labBtn) {
    elements.labBtn.disabled = false
}
if (elements.cvpStatus) {
    elements.cvpStatus.textContent = ""
}
if (elements.cvpLink) {
    elements.cvpLink.classList.remove('cvp-disabled');
    if (elements.cvpLinkAnchor) {
        elements.cvpLinkAnchor.href = '/cv';
        elements.cvpLinkAnchor.target = '_blank';
    }
}
```

## Step 2: Block clicks on disabled CVP link (Critical)

**Problem:** The anchor has `menu-click` class but no `pointer-events: none` when disabled. jQuery handlers still fire on click, and on mobile it toggles the sidebar.

**Fix:** In `home.css`, add `pointer-events: none` to the disabled state rule:

```css
.cvp-sidebar-link.cvp-disabled .site-sidebar__item {
  color: #6c8eaf;
  cursor: not-allowed;
  opacity: 0.5;
  pointer-events: none;
}
```

## Step 3: Add accessibility attributes (Major)

**Problem:** `<a>` without `href` is invisible to some screen readers and skipped in tab order. Tooltip text is not surfaced to assistive tech.

**Fix in index.html:**
```html
<li id="cvpLink" class="cvp-sidebar-link cvp-disabled">
  <a id="cvpLinkAnchor" class="site-sidebar__item menu-click"
     tabindex="-1" aria-disabled="true" aria-label="CVP is starting, please wait">
    <i class="fa-solid fa-spinner fa-spin cvp-spinner" aria-hidden="true"></i>
    <span>CVP</span>
  </a>
  <span class="cvp-tooltip" role="tooltip">CVP is starting, please wait...</span>
</li>
```

**Fix in atd-ws.js** — toggle `tabindex` and `aria-disabled` alongside the class:
```javascript
// When disabling:
elements.cvpLinkAnchor.setAttribute('tabindex', '-1');
elements.cvpLinkAnchor.setAttribute('aria-disabled', 'true');
elements.cvpLinkAnchor.setAttribute('aria-label', 'CVP is starting, please wait');

// When enabling:
elements.cvpLinkAnchor.removeAttribute('tabindex');
elements.cvpLinkAnchor.removeAttribute('aria-disabled');
elements.cvpLinkAnchor.setAttribute('aria-label', 'CVP');
```

## Step 4: Flip spinner display logic (Minor)

**Problem:** Spinner uses `:not(.cvp-disabled)` to hide — fragile if class is used elsewhere.

**Fix in home.css:**
```css
.cvp-spinner {
  font-size: 14px;
  color: var(--secondary-color);
  margin-right: 8px;
  display: none;
}

.cvp-sidebar-link.cvp-disabled .cvp-spinner {
  display: inline-block;
}
```

Remove the existing `.cvp-sidebar-link:not(.cvp-disabled) .cvp-spinner` rule.

## Step 5: Extract hard-coded color to CSS variable (Minor)

**Problem:** `#6c8eaf` appears in 3+ places without a variable.

**Fix:** Add `--muted-text: #6c8eaf;` to the `:root` variables in `variables.css`, then replace all instances.

## Step 6: Verify minification pipeline

**Action:** Confirm the Docker build for `uilanding` regenerates `.min.css` and `.min.js` files from sources. Check `build.sh` and `package.json`. If not automated, add a build step or document the manual process.

---

## Verification

After all fixes:
- [ ] Test with a topology that has CVP enabled — link should start disabled, enable when CVP is up
- [ ] Test with a topology that has CVP disabled (`"cvp" in disable_links`) — no errors in console
- [ ] Test on mobile breakpoint — tooltip appears below, disabled link doesn't toggle sidebar
- [ ] Test keyboard navigation — disabled link is not focusable, enabled link is
- [ ] Screen reader test — disabled state announced, tooltip text accessible
