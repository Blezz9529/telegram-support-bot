/*
  Lightweight loader for the chat widget.
  Goals:
  - Inject the widget iframe only after the main page finishes loading
  - Do not block rendering; no network work before window.load/requestIdleCallback
  - Auto-resize iframe when widget is opened/closed (via postMessage `widget:state`)
  - Optionally auto-load widget-host bridge for title/favicon/sound notifications

  Usage on any site (hosted by nginx from project public root):
    <script src="/widget-loader.js" async data-widget-src="/widget/" data-host-bridge="/widget-host.js"></script>
*/
(function () {
  var script = document.currentScript;
  var widgetSrc = (script && script.getAttribute('data-widget-src')) || '/widget/';
  var hostBridge = (script && script.getAttribute('data-host-bridge')) || '/widget-host.js';
  var z = 2147483000; // very high z-index to be above most UIs
  var closedSize = { w: 72, h: 72 };
  var openSize = { w: 400, h: 560 };
  var container, iframe;
  var resizeIdle;

  function onReady(fn) {
    if (document.readyState === 'complete') return fn();
    window.addEventListener('load', fn, { once: true });
  }

  function idle(fn) {
    if ('requestIdleCallback' in window) {
      (window).requestIdleCallback(fn, { timeout: 1500 });
    } else {
      setTimeout(fn, 0);
    }
  }

  function ensureHostBridge() {
    // load only once per page
    if (document.querySelector('script[data-widget-host-bridge]')) return;
    var s = document.createElement('script');
    s.src = hostBridge;
    s.async = true;
    s.setAttribute('data-widget-host-bridge', '1');
    document.head.appendChild(s);
  }

  function setSize(open) {
    if (!iframe || !container) return;
    var size = open ? openSize : closedSize;
    container.style.width = size.w + 'px';
    container.style.height = size.h + 'px';
    iframe.style.width = '100%';
    iframe.style.height = '100%';
  }

  function scheduleSize(open) {
    if (resizeIdle) cancelIdleCallback(resizeIdle);
    resizeIdle = requestIdleCallback(function () { setSize(open); }, { timeout: 500 });
  }

  function mount() {
    ensureHostBridge();

    // container
    container = document.createElement('div');
    container.id = 'support-widget-container';
    container.style.position = 'fixed';
    container.style.right = '18px';
    container.style.bottom = '18px';
    container.style.width = closedSize.w + 'px';
    container.style.height = closedSize.h + 'px';
    container.style.zIndex = String(z);
    container.style.pointerEvents = 'auto';
    container.style.border = '0';
    container.style.margin = '0';
    container.style.padding = '0';
    container.style.background = 'transparent';

    // iframe
    iframe = document.createElement('iframe');
    iframe.src = widgetSrc;
    iframe.title = 'Support Chat';
    iframe.allow = 'autoplay; clipboard-read; clipboard-write';
    iframe.style.border = '0';
    iframe.style.width = '100%';
    iframe.style.height = '100%';
    iframe.style.background = 'transparent';
    iframe.referrerPolicy = 'no-referrer-when-downgrade';
    container.appendChild(iframe);

    document.body.appendChild(container);

    // listen widget open/close to resize
    window.addEventListener('message', function (event) {
      var data = event && event.data;
      if (!data || (data.type !== 'widget:state')) return;
      var isOpen = !!data.isOpen;
      scheduleSize(isOpen);
    });
  }

  onReady(function () { idle(mount); });
})();

