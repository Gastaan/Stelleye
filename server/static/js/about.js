// About page: the medicine label starts blurred, the way low vision can blur it; one button shows it clearly.
const button = document.getElementById("clear-label");
const label = document.getElementById("med-label");

button.addEventListener("click", () => {
  const clear = button.getAttribute("aria-pressed") !== "true";
  button.setAttribute("aria-pressed", String(clear));
  label.classList.toggle("is-blurred", !clear);
  document.getElementById("clear-label-text").textContent = clear ? "Blur it again" : "Show it clearly";
});
