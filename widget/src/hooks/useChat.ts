import { useState, useCallback, useRef, useEffect } from 'react';
import { ChatMessage, Operator, getSessionId, initSession, fetchMessages, sendMessageToTelegram, connectWebSocket, disconnectWebSocket, getOperatorStatus, fetchUiConfig, fetchSessionState, selectMenu, selectFeedback, closeWidgetSession, clearSessionMedia } from '@/lib/chatService';
// Fallback локали для оффлайн-режима: копии лежат внутри widget/src/locales
import buttons from '@/locales/buttons.json';
import texts from '@/locales/texts.json';

const DEFAULT_OPERATOR: Operator = {
  name: 'Алекс',
  avatar: '',
  status: 'online',
};

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [operator, setOperator] = useState<Operator>(DEFAULT_OPERATOR);
  const [hasUnread, setHasUnread] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isBlocked, setIsBlocked] = useState(false);
  const [menuButtons, setMenuButtons] = useState<string[]>([]);
  const [uiTexts, setUiTexts] = useState<Record<string, string>>(texts as Record<string, string>);
  const [closeLabel, setCloseLabel] = useState<string>((buttons as any).menu.close_ticket || 'Закрыть вопрос');
  const [newDialogLabel, setNewDialogLabel] = useState<string>((buttons as any).menu.new_dialog || 'Новый диалог');
  const [sessionState, setSessionState] = useState<string>('choosing_theme');
  const pendingReplies = useRef(0);
  const isOpenRef = useRef(false);
  const originalTitleRef = useRef<string | null>(null);
  const originalFaviconRef = useRef<string | null>(null);
  const originalFaviconDataUrlRef = useRef<string | null>(null);
  const notifyFaviconDataUrlRef = useRef<string | null>(null);
  const faviconPreloadRef = useRef<Promise<string | null> | null>(null);
  const faviconBlinkRef = useRef<number | null>(null);
  const faviconBlinkStateRef = useRef(false);
  const canBlinkFaviconRef = useRef(false);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const audioUnlockedRef = useRef(false);
  const isFramedRef = useRef(false);
  const unreadStorageKey = 'widget_has_unread';
  const maxImageSize = 5 * 1024 * 1024;
  const allowedImageTypes = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif']);

  const getTargetDocument = () => {
    if (typeof document === 'undefined') return null;
    try {
      if (window.top && window.top.document) {
        return window.top.document;
      }
    } catch {
      // cross-origin, fallback to current document
    }
    return document;
  };

  const postToParent = (
    type: 'widget:notify' | 'widget:clear' | 'widget:state',
    payload?: Record<string, any>
  ) => {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type, ...payload }, '*');
        return true;
      }
    } catch {
      // ignore
    }
    return false;
  };

  const getVisibilityState = () => {
    const doc = getTargetDocument();
    return doc?.visibilityState || 'visible';
  };

  const getFaviconLink = (doc: Document | null) => {
    if (!doc) return null;
    return doc.querySelector<HTMLLinkElement>('link[rel="icon"], link[rel="shortcut icon"]');
  };

  const ensureFaviconLink = () => {
    const doc = getTargetDocument();
    if (!doc) return null;
    let link = getFaviconLink(doc);
    if (!link) {
      link = doc.createElement('link');
      link.rel = 'icon';
      doc.head.appendChild(link);
    }
    return link;
  };

  const fetchFaviconDataUrl = async (href: string): Promise<string | null> => {
    try {
      const res = await fetch(href, { cache: 'force-cache' });
      if (!res.ok) return null;
      const blob = await res.blob();
      return await new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : null);
        reader.onerror = () => resolve(null);
        reader.readAsDataURL(blob);
      });
    } catch {
      return null;
    }
  };

  const preloadFavicon = () => {
    if (faviconPreloadRef.current) return faviconPreloadRef.current;
    const link = ensureFaviconLink();
    const href = link?.href;
    if (!href) {
      faviconPreloadRef.current = Promise.resolve(null);
      return faviconPreloadRef.current;
    }
    faviconPreloadRef.current = fetchFaviconDataUrl(href).then((dataUrl) => {
      if (dataUrl) {
        originalFaviconDataUrlRef.current = dataUrl;
        canBlinkFaviconRef.current = true;
        setFavicon(dataUrl);
      } else {
        canBlinkFaviconRef.current = false;
      }
      return dataUrl;
    });
    return faviconPreloadRef.current;
  };

  const ensureOriginalTitle = () => {
    const doc = getTargetDocument();
    if (originalTitleRef.current === null && doc) {
      originalTitleRef.current = doc.title || 'Support';
    }
  };

  const ensureOriginalFavicon = () => {
    if (originalFaviconRef.current === null) {
      const link = ensureFaviconLink();
      if (link?.href) {
        originalFaviconRef.current = link.href;
      }
      void preloadFavicon();
    }
  };

  const setFavicon = (href: string | null) => {
    const link = ensureFaviconLink();
    if (!link || !href) return;
    link.href = href;
  };

  const getNotifyFavicon = () => {
    if (!notifyFaviconDataUrlRef.current) {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="31" fill="#ff3b30"/></svg>`;
      notifyFaviconDataUrlRef.current = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
    }
    return notifyFaviconDataUrlRef.current;
  };

  const startFaviconBlink = () => {
    if (faviconBlinkRef.current) return;
    ensureOriginalFavicon();
    if (!canBlinkFaviconRef.current || !originalFaviconDataUrlRef.current) return;
    const original = originalFaviconDataUrlRef.current;
    const notify = getNotifyFavicon();
    faviconBlinkRef.current = window.setInterval(() => {
      faviconBlinkStateRef.current = !faviconBlinkStateRef.current;
      setFavicon(faviconBlinkStateRef.current ? notify : original);
    }, 900);
  };

  const stopFaviconBlink = () => {
    if (faviconBlinkRef.current) {
      window.clearInterval(faviconBlinkRef.current);
      faviconBlinkRef.current = null;
    }
    faviconBlinkStateRef.current = false;
    if (originalFaviconDataUrlRef.current) {
      setFavicon(originalFaviconDataUrlRef.current);
    }
  };

  const setNotifyTitle = () => {
    const doc = getTargetDocument();
    if (!doc) return;
    ensureOriginalTitle();
    doc.title = '• Новое сообщение';
  };

  const restoreTitle = () => {
    const doc = getTargetDocument();
    if (!doc) return;
    if (originalTitleRef.current !== null) {
      doc.title = originalTitleRef.current;
    }
  };

  const unlockAudio = async () => {
    if (audioUnlockedRef.current) return;
    const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
    if (!Ctx) return;
    if (!audioCtxRef.current) {
      audioCtxRef.current = new Ctx();
    }
    try {
      await audioCtxRef.current.resume();
      audioUnlockedRef.current = true;
    } catch {
      // ignore
    }
  };

  const playChime = async () => {
    if (!audioCtxRef.current) return;
    const ctx = audioCtxRef.current;
    if (ctx.state === 'suspended') {
      try {
        await ctx.resume();
        audioUnlockedRef.current = true;
      } catch {
        return;
      }
    }
    if (!audioUnlockedRef.current) return;
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, now);
    osc.frequency.exponentialRampToValueAtTime(660, now + 0.15);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.08, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.3);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.32);
  };

  // Initialize session on mount
  useEffect(() => {
    const init = async () => {
      try {
        let currentSessionId = getSessionId();

        const config = await fetchUiConfig();
        if (config) {
          setMenuButtons(config.menu_buttons || []);
          setUiTexts(config.texts || (texts as Record<string, string>));
          setCloseLabel(config.close_button_label || closeLabel);
          setNewDialogLabel(config.new_dialog_label || newDialogLabel);
        } else {
          const menu = Object.values((buttons as any).menu || {}).filter((v: any) => typeof v === 'string');
          setMenuButtons(menu);
        }

        if (currentSessionId) {
          try {
            const existingMessages = await fetchMessages();
            if (existingMessages.length > 0) {
              setMessages(existingMessages);
            } else {
              const initResult = await initSession();
              setMessages(initResult.messages);
              currentSessionId = initResult.session_id;
            }
          } catch (e: any) {
            if (e?.code === 'BLOCKED') {
              setIsBlocked(true);
              setMessages([{
                id: 'blocked',
                text: 'Вы заблокированы. Обратитесь к администратору.',
                sender: 'operator',
                timestamp: new Date(),
              }]);
              return;
            }
            throw e;
          }
        } else {
          try {
            const { messages: welcomeMessages } = await initSession();
            setMessages(welcomeMessages);
            currentSessionId = getSessionId();
          } catch (e: any) {
            if (e?.code === 'BLOCKED') {
              setIsBlocked(true);
              setMessages([{
                id: 'blocked',
                text: 'Вы заблокированы. Обратитесь к администратору.',
                sender: 'operator',
                timestamp: new Date(),
              }]);
              return;
            }
            throw e;
          }
        }

        ensureOriginalTitle();
        ensureOriginalFavicon();

        if (currentSessionId) {
          const state = await fetchSessionState(currentSessionId);
          if (state?.is_blocked) {
            setIsBlocked(true);
          } else if (state?.state) {
            setSessionState(state.state);
          }
        }

        if (!isBlocked && currentSessionId) {
          isFramedRef.current = typeof window !== 'undefined' && window.parent !== window;
          const storedUnread = typeof window !== 'undefined' && localStorage.getItem(unreadStorageKey) === '1';
          if (storedUnread) {
            setHasUnread(true);
            const posted = postToParent('widget:notify');
            if (!posted) {
              setNotifyTitle();
              startFaviconBlink();
            }
          }
          connectWebSocket(
            (newMessage) => {
              setMessages((prev) => [...prev, newMessage]);
              const isIncoming = newMessage.sender !== 'user';
              const isVisible = getVisibilityState() === 'visible';
              if (isIncoming && (!isOpenRef.current || !isVisible)) {
                setHasUnread(true);
                if (typeof window !== 'undefined') {
                  localStorage.setItem(unreadStorageKey, '1');
                }
                const posted = postToParent('widget:notify');
                if (!posted) {
                  setNotifyTitle();
                  startFaviconBlink();
                  void playChime();
                }
              }
              setOperator((prev) => ({ ...prev, status: 'online' }));
            },
            (typing) => {
              setOperator((prev) => ({ ...prev, status: typing ? 'typing' : 'online' }));
            }
          );
        }
      } catch (error) {
        console.error('[useChat] Initialization error:', error);
        // Fallback to welcome message
        setMessages([{
          id: 'welcome',
          text: 'Привет! 👋 Чем могу помочь? Задайте ваш вопрос, и я отвечу как можно скорее.',
          sender: 'operator',
          timestamp: new Date(),
        }]);
      } finally {
        // Always set loading to false after init
        setIsLoading(false);
      }
    };

    init();

    // WebSocket подключаем после init внутри init()

    // Update operator status
    getOperatorStatus().then((status) => {
      setOperator((prev) => ({
        ...prev,
        name: status.operator_name,
        status: status.typing ? 'typing' : 'online'
      }));
    });

    // Cleanup on unmount
    return () => {
      disconnectWebSocket();
    };
  }, []);

  useEffect(() => {
    const handleUnlock = () => {
      unlockAudio();
      window.removeEventListener('pointerdown', handleUnlock);
      window.removeEventListener('keydown', handleUnlock);
    };
    window.addEventListener('pointerdown', handleUnlock, { once: true });
    window.addEventListener('keydown', handleUnlock, { once: true });
    return () => {
      window.removeEventListener('pointerdown', handleUnlock);
      window.removeEventListener('keydown', handleUnlock);
    };
  }, []);

  const sendMessage = useCallback(async (text: string, attachment?: File) => {
    if (attachment && (!allowedImageTypes.has(attachment.type) || attachment.size > maxImageSize)) {
      setMessages((prev) => [
        ...prev,
        {
          id: `media-error-${Date.now()}`,
          text: 'Можно отправлять только изображения до 5 МБ.',
          sender: 'operator',
          timestamp: new Date(),
        }
      ]);
      return;
    }

    const userMsg: ChatMessage = {
      id: Date.now(),
      text,
      sender: 'user',
      timestamp: new Date(),
      attachment: attachment
        ? { name: attachment.name, type: attachment.type, isPlaceholder: true }
        : undefined,
    };

    if (isBlocked) {
      return;
    }
    setMessages((prev) => [...prev, userMsg]);

    // Send to API
    try {
      await sendMessageToTelegram(text, attachment);
    } catch (e: any) {
      setMessages((prev) => prev.filter((msg) => msg.id !== userMsg.id));
      if (e?.code === 'BLOCKED') {
        setIsBlocked(true);
        setMessages([{
          id: 'blocked',
          text: 'Вы заблокированы. Обратитесь к администратору.',
            sender: 'operator',
            timestamp: new Date(),
        }]);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: `send-error-${Date.now()}`,
            text: e?.message || 'Не удалось отправить сообщение.',
            sender: 'operator',
            timestamp: new Date(),
          }
        ]);
      }
    }

  }, [isBlocked]);

  const handleMenuClick = useCallback(async (label: string) => {
    const session_id = getSessionId();
    if (!session_id || isBlocked) return;
    setMessages((prev) => [...prev, { id: Date.now(), text: label, sender: 'user', timestamp: new Date() }]);
    const res = await selectMenu(session_id, label);
    if (res?.message) {
      setMessages((prev) => [...prev, { id: Date.now() + 1, text: res.message, sender: 'operator', timestamp: new Date() }]);
    }
    if (res?.ok) {
      setSessionState(res?.message === uiTexts.feedback_type_question ? 'choosing_feedback_type' : 'in_conversation');
    }
  }, [isBlocked, uiTexts]);

  const handleFeedbackClick = useCallback(async (label: string) => {
    const session_id = getSessionId();
    if (!session_id || isBlocked) return;
    setMessages((prev) => [...prev, { id: Date.now(), text: label, sender: 'user', timestamp: new Date() }]);
    const res = await selectFeedback(session_id, label);
    if (res?.message) {
      setMessages((prev) => [...prev, { id: Date.now() + 1, text: res.message, sender: 'operator', timestamp: new Date() }]);
    }
    if (res?.ok) {
      setSessionState('in_conversation');
    }
  }, [isBlocked]);

  const handleClose = useCallback(async () => {
    const session_id = getSessionId();
    if (!session_id) return;
    await closeWidgetSession(session_id);
    setSessionState('closed');
  }, []);

  const handleNewDialog = useCallback(async () => {
    const currentSessionId = getSessionId();
    if (currentSessionId) {
      await clearSessionMedia(currentSessionId);
    }
    localStorage.removeItem('widget_session_id');
    setMessages([]);
    const { messages: welcomeMessages } = await initSession();
    setMessages(welcomeMessages);
    setSessionState('choosing_theme');
  }, []);

  const openChat = useCallback(() => {
    setIsOpen(true);
    isOpenRef.current = true;
    setHasUnread(false);
    if (typeof window !== 'undefined') {
      localStorage.removeItem(unreadStorageKey);
    }
    const posted = postToParent('widget:clear');
    if (!posted) {
      restoreTitle();
      stopFaviconBlink();
      unlockAudio();
    }
  }, []);

  const closeChat = useCallback(() => {
    setIsOpen(false);
    isOpenRef.current = false;
  }, []);

  useEffect(() => {
    postToParent('widget:state', { isOpen: isOpenRef.current });
  }, [isOpen]);

  return { 
    messages, 
    operator, 
    hasUnread, 
    isOpen, 
    isLoading,
    sendMessage, 
    openChat, 
    closeChat,
    menuButtons,
    sessionState,
    uiTexts,
    closeLabel,
    newDialogLabel,
    handleMenuClick,
    handleFeedbackClick,
    handleClose,
    handleNewDialog
  };
}
