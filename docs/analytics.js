// Google Analytics 4, loaded by every page. The measurement ID is set here and nowhere else.
// Off until ID is set, and off on local previews (localhost, LAN addresses, file://), so only visits to
// the published site are counted. Page views in the raw-data viewer (history changes) are counted by
// GA4's enhanced measurement; nothing else is sent.
(function () {
  var ID = "G-5HMDB4XGGG";               // GA4 measurement ID: Admin > Data streams > context-bench site
  var h = location.hostname;
  var local = !h || h === "localhost" || /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h) ||
              /\.(local|lan|home)$/.test(h);
  if (!/^G-[A-Z0-9]+$/.test(ID) || local) return;
  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
  window.gtag("js", new Date());
  window.gtag("config", ID);
  var s = document.createElement("script");
  s.async = true;
  s.src = "https://www.googletagmanager.com/gtag/js?id=" + ID;
  document.head.appendChild(s);
})();
