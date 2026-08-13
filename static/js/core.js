(function () {
    'use strict';

    const root = document.documentElement;
    const body = document.body;
    const themeToggle = document.getElementById('theme-toggle');
    const storageKey = 'scav-theme';

    function applyTheme(theme, persist) {
        const nextTheme = theme === 'light' ? 'light' : 'dark';
        root.dataset.theme = nextTheme;
        if (themeToggle) {
            themeToggle.setAttribute('aria-pressed', String(nextTheme === 'light'));
            themeToggle.setAttribute('aria-label', nextTheme === 'dark' ? 'Use light theme' : 'Use dark theme');
        }
        if (persist) {
            try {
                localStorage.setItem(storageKey, nextTheme);
            } catch (error) {}
        }
    }

    applyTheme(root.dataset.theme || 'dark', false);

    if (themeToggle) {
        themeToggle.addEventListener('click', function () {
            applyTheme(root.dataset.theme === 'dark' ? 'light' : 'dark', true);
        });
    }

    const profileTrigger = document.querySelector('.profile-menu__trigger');
    const profilePopover = document.querySelector('.profile-menu__popover');

    function closeProfileMenu(returnFocus) {
        if (!profileTrigger || !profilePopover) return;
        profileTrigger.setAttribute('aria-expanded', 'false');
        profilePopover.removeAttribute('data-open');
        if (returnFocus) profileTrigger.focus();
    }

    if (profileTrigger && profilePopover) {
        profileTrigger.addEventListener('click', function () {
            const shouldOpen = profileTrigger.getAttribute('aria-expanded') !== 'true';
            profileTrigger.setAttribute('aria-expanded', String(shouldOpen));
            profilePopover.toggleAttribute('data-open', shouldOpen);
            if (shouldOpen) {
                const firstItem = profilePopover.querySelector('[role="menuitem"]');
                if (firstItem) firstItem.focus();
            }
        });
        document.addEventListener('pointerdown', function (event) {
            if (!profilePopover.contains(event.target) && !profileTrigger.contains(event.target)) closeProfileMenu(false);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && profilePopover.hasAttribute('data-open')) closeProfileMenu(true);
        });
    }

    const countdown = document.querySelector('[data-countdown]');
    if (countdown && countdown.dataset.countdown) {
        const target = new Date(countdown.dataset.countdown);
        function pad(value) { return String(value).padStart(2, '0'); }
        function updateCountdown() {
            const difference = target.getTime() - Date.now();
            if (difference <= 0) {
                countdown.textContent = 'Hunt ended';
                countdown.dataset.state = 'ended';
                return;
            }
            const secondsTotal = Math.floor(difference / 1000);
            const days = Math.floor(secondsTotal / 86400);
            const hours = Math.floor((secondsTotal % 86400) / 3600);
            const minutes = Math.floor((secondsTotal % 3600) / 60);
            const seconds = secondsTotal % 60;
            countdown.textContent = (days ? days + 'd ' : '') + pad(hours) + ':' + pad(minutes) + ':' + pad(seconds);
        }
        updateCountdown();
        window.setInterval(updateCountdown, 1000);
    }

    const tabs = Array.from(document.querySelectorAll('[role="tab"][data-tab]'));
    if (tabs.length) {
        function activateTab(tab, focus) {
            tabs.forEach(function (candidate) {
                const selected = candidate === tab;
                candidate.classList.toggle('active', selected);
                candidate.setAttribute('aria-selected', String(selected));
                candidate.tabIndex = selected ? 0 : -1;
                const panel = document.getElementById(candidate.getAttribute('aria-controls'));
                if (panel) {
                    panel.classList.toggle('active', selected);
                    panel.hidden = !selected;
                }
            });
            if (focus) tab.focus();
        }
        tabs.forEach(function (tab, index) {
            tab.addEventListener('click', function () { activateTab(tab, false); });
            tab.addEventListener('keydown', function (event) {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                let nextIndex = index;
                if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
                if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
                if (event.key === 'Home') nextIndex = 0;
                if (event.key === 'End') nextIndex = tabs.length - 1;
                activateTab(tabs[nextIndex], true);
            });
        });
    }

    const toastContainer = document.querySelector('[data-toast-container]');
    if (toastContainer) {
        function dismissToast(toast, delay) {
            window.setTimeout(function () {
                if (!toast.isConnected) return;
                toast.classList.add('toast--closing');
                window.setTimeout(function () {
                    toast.remove();
                    if (!toastContainer.querySelector('[data-toast]')) toastContainer.hidden = true;
                }, 180);
            }, delay || 0);
        }
        toastContainer.querySelectorAll('[data-toast]').forEach(function (toast, index) {
            const timer = window.setTimeout(function () { dismissToast(toast, 0); }, 4500 + index * 500);
            const close = toast.querySelector('[data-toast-close]');
            if (close) close.addEventListener('click', function () { window.clearTimeout(timer); dismissToast(toast, 0); });
        });
    }

    const termsOverlay = document.querySelector('[data-terms-overlay]');
    if (termsOverlay) {
        const content = termsOverlay.querySelector('[data-terms-content]');
        const accept = termsOverlay.querySelector('[data-terms-accept]');
        const helper = termsOverlay.querySelector('[data-terms-helper]');
        const participantId = body.dataset.participantId || 'anonymous';
        const huntYear = body.dataset.huntYear || 'na';
        const termsStorageKey = 'scav-terms:' + huntYear + ':' + participantId;
        let accepted = false;
        try { accepted = localStorage.getItem(termsStorageKey) === 'true'; } catch (error) {}
        if (!accepted) {
            try { accepted = sessionStorage.getItem(termsStorageKey) === 'true'; } catch (error) {}
        }

        function hideTerms() {
            termsOverlay.hidden = true;
            body.classList.remove('modal-open');
        }

        if (accepted) {
            hideTerms();
        } else if (content && accept && helper) {
            termsOverlay.hidden = false;
            body.classList.add('modal-open');
            window.requestAnimationFrame(function () { content.focus(); });
            function checkScroll() {
                const atBottom = content.scrollTop + content.clientHeight >= content.scrollHeight - 4;
                if (atBottom) {
                    accept.disabled = false;
                    helper.textContent = 'You can now acknowledge the rules and continue.';
                }
            }
            if (content.scrollHeight <= content.clientHeight + 4) {
                accept.disabled = false;
                helper.textContent = 'Review the rules, then acknowledge to continue.';
            } else {
                content.addEventListener('scroll', checkScroll, {passive: true});
            }
            checkScroll();
            accept.addEventListener('click', function () {
                let saved = false;
                try { localStorage.setItem(termsStorageKey, 'true'); saved = true; } catch (error) {}
                if (!saved) {
                    try { sessionStorage.setItem(termsStorageKey, 'true'); } catch (error) {}
                }
                hideTerms();
            });
            termsOverlay.addEventListener('keydown', function (event) {
                if (event.key !== 'Tab') return;
                const focusable = Array.from(termsOverlay.querySelectorAll('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'));
                if (!focusable.length) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
            });
        }
    }

    const snowOverlay = document.querySelector('.snow-overlay');
    if (snowOverlay) {
        for (let index = 0; index < 64; index += 1) {
            const flake = document.createElement('span');
            flake.className = 'snowflake';
            flake.style.setProperty('--x', (Math.random() * 100).toFixed(2) + '%');
            flake.style.setProperty('--size', (Math.random() * 3 + 2).toFixed(2) + 'px');
            flake.style.setProperty('--duration', (Math.random() * 8 + 8).toFixed(2) + 's');
            flake.style.setProperty('--delay', (Math.random() * -12).toFixed(2) + 's');
            snowOverlay.appendChild(flake);
        }
    }
})();
