// The posting form's "Posting…" state: once the browser has accepted the
// form and is sending it, the submit button shows a spinner (keel.css) and a
// second click is ignored. Without this file the form posts exactly the same;
// the submission_key already makes a repeated post harmless.
(() => {
  const form = document.querySelector('form[action="/post-transaction"]');
  if (!form) return;
  const button = form.querySelector('button[type="submit"]');
  const label = button.textContent;

  form.addEventListener("submit", (event) => {
    if (button.classList.contains("is-busy")) {
      event.preventDefault();
      return;
    }
    button.classList.add("is-busy");
    button.setAttribute("aria-disabled", "true");
    button.textContent = "Posting…";
  });

  // Back from the next page, the browser may restore this one as it was left:
  // busy. Put the button back.
  window.addEventListener("pageshow", () => {
    button.classList.remove("is-busy");
    button.removeAttribute("aria-disabled");
    button.textContent = label;
  });
})();
