// Alumni Connect: small helpers. Every page still works with JavaScript off.
(function () {
  "use strict";

  // Close buttons on flash messages
  document.querySelectorAll(".flash-close").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var flash = btn.closest(".flash");
      if (flash) flash.remove();
    });
  });

  // Ask before a destructive action: <button data-confirm="Reject Asha Rao?">
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var submitter = event.submitter;
      var question = submitter && submitter.getAttribute("data-confirm");
      if (question && !window.confirm(question)) {
        event.preventDefault();
        return;
      }
      // Stop double submits (two requests sent, two approvals). The delay keeps
      // the clicked button's name/value in the submitted data.
      window.setTimeout(function () {
        form.querySelectorAll("button[type=submit], button:not([type])").forEach(function (b) {
          b.disabled = true;
        });
      }, 0);
    });
  });

  // Back button restores the page from cache with buttons still disabled
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      document.querySelectorAll("form button:disabled").forEach(function (b) {
        b.disabled = false;
      });
    }
  });

  // Live character counter: <textarea data-counter="counter-id" maxlength="500">
  document.querySelectorAll("textarea[data-counter]").forEach(function (area) {
    var out = document.getElementById(area.getAttribute("data-counter"));
    if (!out) return;
    var max = area.getAttribute("maxlength");
    function update() {
      out.textContent = area.value.length + (max ? " / " + max : "");
    }
    area.addEventListener("input", update);
    update();
  });

  // Register page: the two passwords must match
  var password = document.getElementById("password");
  var confirm = document.getElementById("confirm_password");
  if (password && confirm) {
    function check() {
      confirm.setCustomValidity(
        confirm.value && confirm.value !== password.value ? "The passwords don't match." : ""
      );
    }
    password.addEventListener("input", check);
    confirm.addEventListener("input", check);
  }
})();
