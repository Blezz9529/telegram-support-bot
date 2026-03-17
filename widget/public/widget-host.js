(() => {
  const script = document.currentScript;
  const allowedOrigins = (script?.dataset.widgetOrigin || location.origin)
    .split(',')
    .map((o) => o.trim())
    .filter(Boolean);

  const state = {
    originalTitle: document.title || 'Support',
    originalFavicon: null,
    originalFaviconDataUrl: null,
    notifyFaviconDataUrl: null,
    faviconPreload: null,
    blinkInterval: null,
    blinkOn: false,
    audioCtx: null,
    unlocked: false,
    widgetOpen: false,
    canBlinkFavicon: false,
  };

  const getFaviconLink = () =>
    document.querySelector('link[rel="icon"], link[rel="shortcut icon"]');

  const ensureFaviconLink = () => {
    let link = getFaviconLink();
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    // Remove malformed attributes that look like URLs
    Array.from(link.attributes).forEach((attr) => {
      if (/^https?:\/\//.test(attr.name)) {
        link.removeAttribute(attr.name);
      }
    });
    return link;
  };

  const fetchFaviconDataUrl = async (href) => {
    try {
      const res = await fetch(href, { cache: 'force-cache' });
      if (!res.ok) return null;
      const blob = await res.blob();
      return await new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => resolve(null);
        reader.readAsDataURL(blob);
      });
    } catch {
      return null;
    }
  };

  const preloadFavicon = () => {
    if (state.faviconPreload) return state.faviconPreload;
    const link = ensureFaviconLink();
    const href = link?.href || `${location.origin}/favicon.ico`;
    state.faviconPreload = fetchFaviconDataUrl(href).then((dataUrl) => {
      if (dataUrl) {
        state.originalFaviconDataUrl = dataUrl;
        state.canBlinkFavicon = true;
        link.href = dataUrl;
      }
      return dataUrl;
    });
    return state.faviconPreload;
  };

  const ensureOriginalFavicon = () => {
    if (!state.originalFavicon) {
      const link = ensureFaviconLink();
      const current = link?.href || '';
      if (current && !/\/widget\/?$/.test(current)) {
        state.originalFavicon = current;
      } else {
        // If source favicon is invalid, keep favicon untouched and fall back to title only.
        state.originalFavicon = null;
      }
      preloadFavicon();
    }
  };

  const setFavicon = (href) => {
    const link = ensureFaviconLink();
    if (!link) return;
    if (!href) return;
    link.href = href;
  };

  const notifyFavicon = () => {
    if (!state.notifyFaviconDataUrl) {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
        <circle cx="32" cy="32" r="31" fill="#ff3b30"/>
      </svg>`;
      state.notifyFaviconDataUrl = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
    }
    return state.notifyFaviconDataUrl;
  };

  const startBlink = () => {
    if (state.blinkInterval) return;
    ensureOriginalFavicon();
    if (!state.canBlinkFavicon || !state.originalFaviconDataUrl) return;
    const original = state.originalFaviconDataUrl;
    const notify = notifyFavicon();
    state.blinkInterval = setInterval(() => {
      state.blinkOn = !state.blinkOn;
      setFavicon(state.blinkOn ? notify : original);
    }, 900);
  };

  const stopBlink = () => {
    if (state.blinkInterval) {
      clearInterval(state.blinkInterval);
      state.blinkInterval = null;
    }
    state.blinkOn = false;
    if (state.originalFaviconDataUrl) {
      setFavicon(state.originalFaviconDataUrl);
    }
  };

  const setTitle = () => {
    document.title = '• Новое сообщение';
  };

  const restoreTitle = () => {
    document.title = state.originalTitle;
  };

  const ensureAudio = () => {
    if (!state.audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      state.audioCtx = new Ctx();
    }
    return state.audioCtx;
  };

  const unlockAudio = async () => {
    const ctx = ensureAudio();
    if (!ctx) return;
    try {
      await ctx.resume();
      state.unlocked = true;
    } catch {
      // ignore
    }
  };

  const playChime = async () => {
    const ctx = ensureAudio();
    if (!ctx) return;
    if (ctx.state === 'suspended') {
      try {
        await ctx.resume();
      } catch {
        return;
      }
    }
    if (!state.unlocked) return;
    const now = ctx.currentTime;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.2, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.4);

    const osc1 = ctx.createOscillator();
    osc1.type = 'triangle';
    osc1.frequency.setValueAtTime(880, now);
    osc1.connect(gain);

    const osc2 = ctx.createOscillator();
    osc2.type = 'triangle';
    osc2.frequency.setValueAtTime(660, now + 0.14);
    osc2.connect(gain);

    gain.connect(ctx.destination);
    osc1.start(now);
    osc1.stop(now + 0.18);
    osc2.start(now + 0.18);
    osc2.stop(now + 0.36);
  };

  const originAllowed = (origin) =>
    allowedOrigins.includes('*') || allowedOrigins.includes(origin);

  window.addEventListener('message', (event) => {
    if (!originAllowed(event.origin)) return;
    const { type } = event.data || {};
    if (type === 'widget:notify') {
      const visible = document.visibilityState === 'visible';
      if (state.widgetOpen && visible) return;
      setTitle();
      startBlink();
      void playChime();
    } else if (type === 'widget:clear') {
      restoreTitle();
      stopBlink();
    } else if (type === 'widget:state') {
      state.widgetOpen = !!event.data.isOpen;
    }
  });

  const unlockHandler = () => {
    void unlockAudio();
    window.removeEventListener('pointerdown', unlockHandler);
    window.removeEventListener('keydown', unlockHandler);
  };
  window.addEventListener('pointerdown', unlockHandler, { once: true });
  window.addEventListener('keydown', unlockHandler, { once: true });
})();
