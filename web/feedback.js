"use strict";

const bookFeedback = (() => {
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const animations = new Set();
  const controls = ".search-button, .action-button, .icon-button, .radar-launcher, .ocean-launcher, .radar-segments button, .radar-chip, .ocean-preset, .ocean-auto, .ocean-reset";

  function animate(element, frames, options) {
    if (!element || reducedMotion.matches || !element.animate) return;
    const animation = element.animate(frames, options);
    animations.add(animation);
    animation.finished.catch(() => {}).finally(() => animations.delete(animation));
    return animation;
  }
  function ripple(control, event) {
    if (reducedMotion.matches || control.disabled || control.getAttribute("aria-disabled") === "true") return;
    control.querySelectorAll(":scope > .tap-ripple").forEach(node => node.remove());
    const bounds = control.getBoundingClientRect();
    const size = Math.hypot(bounds.width, bounds.height) * 2;
    const wave = document.createElement("span");
    wave.className = "tap-ripple";
    wave.setAttribute("aria-hidden", "true");
    wave.style.width = wave.style.height = size + "px";
    const x = event?.detail || event?.type === "pointerdown" ? event.clientX - bounds.left : bounds.width / 2;
    const y = event?.detail || event?.type === "pointerdown" ? event.clientY - bounds.top : bounds.height / 2;
    wave.style.left = x - size / 2 + "px";
    wave.style.top = y - size / 2 + "px";
    // Prepending preserves existing labels, including the download link's last text node.
    control.prepend(wave);
    wave.addEventListener("animationend", () => wave.remove(), { once: true });
    setTimeout(() => wave.remove(), 600);
  }
  document.addEventListener("pointerdown", event => {
    if (event.button !== 0 || event.isPrimary === false) return;
    const control = event.target.closest(controls);
    if (control) ripple(control, event);
  });
  document.addEventListener("click", event => {
    if (event.detail !== 0) return;
    const control = event.target.closest(controls);
    if (control) ripple(control, event);
  }, true);
  reducedMotion.addEventListener("change", () => {
    if (!reducedMotion.matches) return;
    animations.forEach(animation => animation.cancel());
    document.querySelectorAll(".tap-ripple").forEach(node => node.remove());
  });
  return {
    reveal(element, duration = 180) {
      return animate(element, [{ opacity: .6, transform: "translateY(4px)" }, { opacity: 1, transform: "translateY(0)" }], { duration, easing: "ease-out" });
    },
    confirm(element) {
      animate(element, [{ boxShadow: "0 0 0 0 rgba(61, 125, 135, .28)" }, { boxShadow: "0 0 0 5px rgba(61, 125, 135, 0)" }], { duration: 420, easing: "ease-out" });
    }
  };
})();
