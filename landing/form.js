(function () {
  "use strict";
  var form = document.getElementById("demo-form");
  var statusEl = document.getElementById("form-status");
  var config = window.THRENDA_SITE_CONFIG || {};

  function show(message, ok) {
    statusEl.textContent = message;
    statusEl.className = "status " + (ok ? "ok" : "err");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!form.checkValidity()) {
      show("Kérjük, töltsd ki a nevet, egy érvényes e-mail címet, a cégnevet, és fogadd el a hozzájárulást.", false);
      return;
    }
    var data = new FormData(form);
    if (data.get("website")) {
      // Honeypot filled in: silently pretend success to the bot.
      show("Köszönjük! Hamarosan jelentkezünk.", true);
      return;
    }
    data.delete("website");

    if (config.formEndpoint) {
      var button = form.querySelector("button[type=submit]");
      button.disabled = true;
      fetch(config.formEndpoint, {
        method: "POST",
        body: data,
        headers: { Accept: "application/json" }
      })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          form.reset();
          show("Köszönjük! Megkaptuk a demókérést, hamarosan jelentkezünk.", true);
        })
        .catch(function () {
          show("A küldés nem sikerült. Kérjük, próbáld újra később" +
            (config.contactEmail ? ", vagy írj nekünk: " + config.contactEmail : "") + ".", false);
        })
        .finally(function () { button.disabled = false; });
      return;
    }

    if (config.contactEmail) {
      var body = "Név: " + data.get("name") + "\nE-mail: " + data.get("email") +
        "\nCég: " + data.get("company") + "\nCsapatméret: " + (data.get("team_size") || "-") +
        "\n\n" + (data.get("message") || "");
      window.location.href = "mailto:" + encodeURIComponent(config.contactEmail) +
        "?subject=" + encodeURIComponent("Threnda demókérés") + "&body=" + encodeURIComponent(body);
      show("Megnyitottuk a levelezőprogramodat az előre kitöltött kéréssel — küldd el onnan.", true);
      return;
    }

    show("A demókérés jelenleg nem elérhető (az űrlap kézbesítése nincs beállítva).", false);
  });
})();
