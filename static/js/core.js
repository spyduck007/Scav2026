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
    function dismissToast(toast, delay) {
        window.setTimeout(function () {
            if (!toast.isConnected) return;
            toast.classList.add('toast--closing');
            window.setTimeout(function () {
                toast.remove();
                if (toastContainer && !toastContainer.querySelector('[data-toast]')) toastContainer.hidden = true;
            }, 180);
        }, delay || 0);
    }
    function wireToast(toast) {
        const timer = window.setTimeout(function () { dismissToast(toast, 0); }, 4500);
        const close = toast.querySelector('[data-toast-close]');
        if (close) close.addEventListener('click', function () { window.clearTimeout(timer); dismissToast(toast, 0); });
    }
    function showToast(message, tag) {
        if (!toastContainer) return;
        const toast = document.createElement('div');
        toast.className = 'toast toast--' + (tag || 'info');
        toast.setAttribute('data-toast', '');
        toast.setAttribute('role', 'status');
        toast.innerHTML = '<span class="toast__indicator" aria-hidden="true"></span>'
            + '<div class="toast__content"></div>'
            + '<button type="button" class="toast__close" data-toast-close aria-label="Dismiss notification">'
            + '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"/></svg></button>';
        toast.querySelector('.toast__content').textContent = message;
        toastContainer.hidden = false;
        toastContainer.appendChild(toast);
        wireToast(toast);
    }
    if (toastContainer) {
        toastContainer.querySelectorAll('[data-toast]').forEach(wireToast);
    }

    function copyFallback(value) {
        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        let copied = false;
        try { copied = document.execCommand('copy'); } catch (error) {}
        textarea.remove();
        return copied;
    }

    document.querySelectorAll('[data-copy-link]').forEach(function (button) {
        button.addEventListener('click', function () {
            const value = button.dataset.copyLink;
            const copied = function () {
                const label = button.textContent;
                button.textContent = 'Copied';
                window.setTimeout(function () {
                    if (button.isConnected) button.textContent = label;
                }, 1400);
            };
            const failed = function () { showToast('Unable to copy short link.', 'error'); };
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(value).then(copied).catch(function () {
                    if (!copyFallback(value)) failed();
                    else copied();
                });
            } else if (!copyFallback(value)) {
                failed();
            } else {
                copied();
            }
        });
    });

    const STATUS_TAGS = { correct: 'success', already_solved: 'info' };

    function pluralize(count, word) {
        return count + ' ' + word + (count === 1 ? '' : 's');
    }

    function renderLeaderboard(entries) {
        const list = document.querySelector('.standings-panel .ranking-list--compact');
        if (!list || !entries) return;
        list.innerHTML = entries.map(function (entry) {
            const rank = String(entry.rank).padStart(2, '0');
            const current = entry.is_user_team ? ' ranking-row--current' : '';
            return '<li class="ranking-row' + current + '">'
                + '<span class="ranking-row__rank">' + rank + '</span>'
                + '<span class="ranking-row__team">' + entry.label + '<small>' + pluralize(entry.member_count, 'member') + '</small></span>'
                + '<strong>' + entry.score + '</strong>'
                + '</li>';
        }).join('');
    }

    function markChallengeSolved(slug, awardedPoints, solvesCount) {
        const card = document.querySelector('.challenge-card[data-challenge-slug="' + slug + '"]');
        if (!card) return;
        card.classList.add('challenge-card--solved');
        card.classList.remove('challenge-card--locked');
        if (typeof solvesCount === 'number') {
            const count = card.querySelector('.solve-count');
            if (count) count.textContent = pluralize(solvesCount, 'solve');
        }
        const form = card.querySelector('.answer-form');
        if (form) {
            const status = document.createElement('div');
            status.className = 'challenge-status challenge-status--success';
            status.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 4 4 8-9"/></svg><span></span>';
            status.querySelector('span').textContent = 'Solved for ' + awardedPoints + ' points';
            form.replaceWith(status);
        }
    }

    document.querySelectorAll('.answer-form').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            const button = form.querySelector('button[type="submit"]');
            const input = form.querySelector('input[name="answer"]');
            const formData = new FormData(form);
            if (button) button.disabled = true;

            fetch(form.action, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            }).then(function (response) {
                if (!response.ok) throw new Error('Unexpected response');
                return response.json();
            }).then(function (data) {
                showToast(data.message, STATUS_TAGS[data.status] || 'error');
                if (input) input.value = '';
                if (button) button.disabled = false;

                if (data.challenge && data.challenge.requires_refresh) {
                    window.setTimeout(function () { window.location.reload(); }, 600);
                    return;
                }
                if (data.status === 'correct' || data.status === 'already_solved') {
                    if (data.challenge) {
                        markChallengeSolved(data.challenge.slug, data.challenge.awarded_points, data.challenge.solves_count);
                    }
                    if (data.leaderboard) renderLeaderboard(data.leaderboard);
                }
            }).catch(function () {
                // Fall back to a normal form submission if the async path fails.
                form.submit();
            });
        });
    });

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
