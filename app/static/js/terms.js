// Opening a term's card on hover, on desktop. Each term (the `term` macro in
// templates/_ui.html) is a native popover button, so click, tap, keyboard and
// Esc all work without this file; it only adds what a mouse expects:
// - resting on a term for 300 ms opens its card;
// - the card stays open while the pointer is over the term or the card, so it
//   can be read and its link reached, and closes 200 ms after leaving both;
// - a card opened by a click, a tap or the keyboard is left alone, and so is
//   one that holds the keyboard focus.
// Only mouse pointers count: a touch's pointerenter would open the card just
// before the tap toggled it shut again.
(() => {
  const OPEN_DELAY = 300;
  const CLOSE_DELAY = 200;

  for (const term of document.querySelectorAll(".term[popovertarget]")) {
    const card = document.getElementById(term.getAttribute("popovertarget"));
    if (!card || typeof card.showPopover !== "function") continue;

    let openTimer;
    let closeTimer;
    let openedByHover = false;
    const isOpen = () => card.matches(":popover-open");

    const enter = (event) => {
      if (event.pointerType !== "mouse") return;
      clearTimeout(closeTimer);
      if (isOpen()) return;
      openTimer = setTimeout(() => {
        if (!isOpen()) {
          card.showPopover();
          openedByHover = true;
        }
      }, OPEN_DELAY);
    };

    const leave = (event) => {
      if (event.pointerType !== "mouse") return;
      clearTimeout(openTimer);
      if (!openedByHover) return;
      closeTimer = setTimeout(() => {
        const over = term.matches(":hover") || card.matches(":hover");
        if (isOpen() && !over && !card.contains(document.activeElement)) card.hidePopover();
      }, CLOSE_DELAY);
    };

    term.addEventListener("pointerenter", enter);
    term.addEventListener("pointerleave", leave);
    card.addEventListener("pointerenter", enter);
    card.addEventListener("pointerleave", leave);
    card.addEventListener("toggle", (event) => {
      if (event.newState === "closed") openedByHover = false;
    });
  }
})();
