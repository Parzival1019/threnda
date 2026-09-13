/*
 * Minimal vanilla JS, no framework, no build step — see CLAUDE.md
 * ("Minimal JavaScript"). Three small enhancements only:
 *   1. `/` focuses the search box, `Esc` closes the open detail panel.
 *   2. The bulk-action bar shows a live "N selected" count and disables
 *      itself when nothing is checked.
 *   3. htmx response-error toast: surface a failed request instead of
 *      silently doing nothing.
 */
(function () {
  "use strict";

  document.addEventListener("keydown", function (event) {
    if (event.key === "/" && !isTypingTarget(event.target)) {
      var search = document.getElementById("search-input");
      if (search) {
        event.preventDefault();
        search.focus();
      }
    }
    if (event.key === "Escape") {
      var closer = document.querySelector("#detail-panel [data-close]");
      if (closer) {
        closer.click();
      }
    }
  });

  function isTypingTarget(el) {
    var tag = el && el.tagName ? el.tagName.toLowerCase() : "";
    return tag === "input" || tag === "textarea" || tag === "select";
  }

  function refreshBulkBar(bar) {
    var form = bar.closest("form");
    if (!form) return;
    var checked = form.querySelectorAll("input[name='ids']:checked");
    var count = checked.length;
    bar.hidden = count === 0;
    var counter = bar.querySelector(".bulk-count");
    if (counter) {
      counter.textContent = count + (count === 1 ? " selected" : " selected");
    }
  }

  // Selecting a row's checkbox must not also trigger the row's own
  // hx-get (which opens the detail panel) — a delegated listener instead
  // of an inline onclick= attribute, so the response security headers can
  // set a strict script-src with no 'unsafe-inline'.
  document.addEventListener("click", function (event) {
    if (event.target.matches && event.target.matches("input[type='checkbox'][name='ids']")) {
      event.stopPropagation();
    }
  });

  document.addEventListener("change", function (event) {
    if (event.target.matches && event.target.matches("input[name='ids']")) {
      var bar = document.getElementById("bulk-bar");
      if (bar) refreshBulkBar(bar);
    }
    if (event.target.matches && event.target.matches("#select-all")) {
      var form = event.target.closest("form");
      if (form) {
        form
          .querySelectorAll("input[name='ids']")
          .forEach(function (box) {
            box.checked = event.target.checked;
          });
        var bulkBar = document.getElementById("bulk-bar");
        if (bulkBar) refreshBulkBar(bulkBar);
      }
    }
  });

  document.body.addEventListener("htmx:responseError", function (event) {
    var target = event.detail.target;
    if (target) {
      target.insertAdjacentHTML(
        "afterbegin",
        '<div class="error-banner">Request failed (' +
          event.detail.xhr.status +
          "). Please try again.</div>"
      );
    }
  });
})();
