/* console_icons.js — React-safe Lucide renderer.
 *
 * Lucide's stock createIcons() REPLACES each <i data-lucide> element with an <svg>.
 * Those <i> are React-rendered, so after a re-render (the console polls /ui/fleet
 * every 5s) React tries to reconcile an <i> that Lucide already removed →
 * "Failed to execute 'removeChild' on 'Node'".
 *
 * Fix: override createIcons to render the SVG as a CHILD of the <i> (innerHTML),
 * never replacing it. React still owns the <i> (whose JSX has no children, so React
 * leaves our injected svg alone) → no node is pulled out from under React. Size/
 * colour are copied from the element's own (React-set) inline style. Idempotent:
 * re-running clears + re-injects. Must load AFTER lucide.min.js, BEFORE app.js. */
(function () {
  "use strict";
  var L = window.lucide;
  if (!L || typeof L.createElement !== "function") return;

  function pascal(kebab) {
    return String(kebab).split("-")
      .map(function (s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; })
      .join("");
  }
  function nodeFor(name) {
    var p = pascal(name);
    return (L.icons && L.icons[p]) || L[p] || null;
  }

  L.createIcons = function () {
    var els;
    try { els = document.querySelectorAll("[data-lucide]"); } catch (e) { return; }
    Array.prototype.forEach.call(els, function (el) {
      var name = el.getAttribute("data-lucide");
      if (!name) return;
      var icon = nodeFor(name);
      if (!icon) return;
      var built;
      try { built = L.createElement(icon); } catch (e) { return; }
      var cs = el.style || {};
      try { el.textContent = ""; } catch (e) { /* ignore */ }
      if (built && built.nodeType === 1) {
        if (cs.width) built.setAttribute("width", parseInt(cs.width, 10) || cs.width);
        if (cs.height) built.setAttribute("height", parseInt(cs.height, 10) || cs.height);
        built.setAttribute("stroke", cs.color || "currentColor");
        el.appendChild(built);
      } else if (typeof built === "string") {
        el.innerHTML = built;
      }
    });
  };
})();
