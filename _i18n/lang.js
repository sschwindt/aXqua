/* aXqua documentation: the language of a page (docs/i18n/inject_i18n.py adds this to every page).
 *
 *   https://axqua.eu/...      English
 *   https://axqua.eu/de/...   German   (axqua.de forwards here)
 *   https://axqua.eu/fr/...   French   (axqua.fr forwards here)
 *
 * The address always wins: a German or French page is never redirected, so a reader who comes
 * through axqua.de or axqua.fr, or follows a link to a translated page, reads that language, and it
 * is remembered. A reader who arrives from outside the site on an English page is sent to the
 * language chosen before, or else to the language of the browser if that is German or French.
 * Following links inside the site redirects nothing. Choosing EN, DE or FR is remembered.
 */
(function () {
  "use strict";

  var me = document.currentScript;
  if (!me) return;
  var lang = me.getAttribute("data-lang") || "en";
  var root = me.getAttribute("data-root") || "";
  var page = me.getAttribute("data-page") || "index.html";
  var KEY = "axqua-lang";
  var NOTE = "axqua-mt-note-closed";

  function read(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function store(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }
  function browserLanguage() {
    var list = navigator.languages && navigator.languages.length ? navigator.languages
      : [navigator.language || "en"];
    var first = String(list[0] || "en").toLowerCase();
    if (first.indexOf("de") === 0) return "de";
    if (first.indexOf("fr") === 0) return "fr";
    return "en";
  }

  var fromInside = false;
  try {
    fromInside = !!document.referrer && new URL(document.referrer).host === location.host;
  } catch (e) { fromInside = false; }

  if (!fromInside) {
    if (lang !== "en") {
      store(KEY, lang);
    } else {
      var wanted = read(KEY) || browserLanguage();
      if (wanted === "de" || wanted === "fr") {
        location.replace(root + wanted + "/" + page + location.search + location.hash);
        return;
      }
    }
  }

  if (read(NOTE) === "1") document.documentElement.className += " axqua-mt-closed";

  // the menu of the language button closes on a click elsewhere and on Escape
  function closeMenus(except) {
    var open = document.querySelectorAll("details.axqua-lang[open]");
    for (var i = 0; i < open.length; i++) {
      if (open[i] !== except) open[i].removeAttribute("open");
    }
  }
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeMenus(null);
  });

  document.addEventListener("click", function (event) {
    var target = event.target;
    closeMenus(target && target.closest ? target.closest("details.axqua-lang") : null);
    var link = target && target.closest ? target.closest("a[data-axqua-lang]") : null;
    if (link) store(KEY, link.getAttribute("data-axqua-lang"));
    var close = target && target.closest ? target.closest(".axqua-mt-note button") : null;
    if (close) {
      store(NOTE, "1");
      document.documentElement.className += " axqua-mt-closed";
    }
  });
})();
