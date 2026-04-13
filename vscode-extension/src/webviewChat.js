// webviewChat.js — runs inside the VS Code/Cursor webview as an external script
(function () {
    'use strict';
    const vscode = acquireVsCodeApi();
    const SC = { '问题分析中': '#FFB547', '问题解决中': '#4F8EF7', '效果验证': '#00D68F', '问题关闭': '#9AA0AC' };

    // ── Event bindings ────────────────────────────────────────────
    document.getElementById('issue-selector-btn').addEventListener('click', toggleDropdown);
    document.getElementById('new-issue-btn').addEventListener('click', toggleNewInput);
    document.getElementById('set-issue-btn').addEventListener('click', setIssue);
    document.getElementById('btn-refresh').addEventListener('click', function () { post('refresh'); });
    document.getElementById('btn-clear').addEventListener('click', clearIssue);
    document.getElementById('btn-record').addEventListener('click', function () { post('recordChange'); });
    document.getElementById('send-btn').addEventListener('click', sendChat);
    document.getElementById('apply-accept-btn').addEventListener('click', function () {
        post('acceptApply');
        document.getElementById('apply-bar').style.display = 'none';
    });
    document.getElementById('apply-reject-btn').addEventListener('click', function () {
        post('rejectApply');
        document.getElementById('apply-bar').style.display = 'none';
    });
    document.getElementById('inp').addEventListener('input', function () { autoResize(this); });
    // Event delegation for dynamic content
    document.getElementById('msgs').addEventListener('click', function (e) {
        const btn = e.target.closest('.btn-apply-code');
        if (btn) applyCode(btn.dataset.codeId);
    });
    document.getElementById('issue-items').addEventListener('click', function (e) {
        const item = e.target.closest('.issue-item');
        if (item) quickOpen(item.dataset.key);
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { closeDropdown(); closeNewInput(); }
        if (e.key === 'Enter') {
            const ik = document.getElementById('ik');
            if (ik && ik === document.activeElement) { e.preventDefault(); setIssue(); return; }
            const inp = document.getElementById('inp');
            if (inp && inp === document.activeElement && !e.shiftKey) { e.preventDefault(); sendChat(); }
        }
    });
    // Close dropdown when clicking outside
    document.addEventListener('click', function (e) {
        if (!e.target.closest('#selector-bar') && !e.target.closest('#issue-dropdown')) {
            closeDropdown();
        }
    });

    // ── Issue selector ────────────────────────────────────────────
    let dropdownOpen = false;

    function toggleDropdown() {
        dropdownOpen = !dropdownOpen;
        document.getElementById('issue-dropdown').style.display = dropdownOpen ? 'block' : 'none';
        if (dropdownOpen) {
            closeNewInput();
            post('fetchRecent');
        }
    }
    function closeDropdown() {
        dropdownOpen = false;
        document.getElementById('issue-dropdown').style.display = 'none';
    }
    function toggleNewInput() {
        const row = document.getElementById('new-issue-row');
        const isShown = row.style.display !== 'none';
        row.style.display = isShown ? 'none' : 'flex';
        if (!isShown) { closeDropdown(); document.getElementById('ik').focus(); }
    }
    function closeNewInput() {
        document.getElementById('new-issue-row').style.display = 'none';
    }

    function post(type, val) {
        vscode.postMessage(val === undefined ? { type } : { type, ...(type === 'openFile' ? { file: val } : { key: val }) });
    }
    function setIssue() {
        const k = (document.getElementById('ik').value || '').trim();
        if (k) { closeDropdown(); closeNewInput(); vscode.postMessage({ type: 'setIssue', key: k }); }
    }
    function quickOpen(key) {
        closeDropdown();
        vscode.postMessage({ type: 'setIssue', key });
    }
    function clearIssue() {
        document.getElementById('issue-header').style.display = 'none';
        document.getElementById('footer').style.display = 'none';
        document.getElementById('msgs').innerHTML = '';
        document.getElementById('ik').value = '';
        document.getElementById('selector-text').textContent = '选择 Issue…';
        document.getElementById('issue-selector-btn').classList.remove('has-issue');
        post('clearIssue');
    }

    // ── Chat ──────────────────────────────────────────────────────
    function sendChat() {
        const inp = document.getElementById('inp');
        const text = inp.value.trim();
        if (!text) return;
        inp.value = ''; autoResize(inp);
        appendMsg('user', '你', text);
        document.getElementById('send-btn').disabled = true;
        vscode.postMessage({ type: 'chat', text });
    }
    function sendAutoChat(text) {
        appendMsg('user', '你', text);
        document.getElementById('send-btn').disabled = true;
        vscode.postMessage({ type: 'chat', text });
    }
    function autoResize(el) {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 160) + 'px';
    }

    // ── Message rendering ─────────────────────────────────────────
    let thinkingEl = null;
    function appendMsg(cls, speaker, text) {
        const msgs = document.getElementById('msgs');
        if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
        const div = document.createElement('div');
        div.className = 'msg ' + cls;
        const body = cls === 'user' ? esc(text) : md(text);
        div.innerHTML = '<div class="msg-speaker">' + esc(speaker) + '</div><div class="msg-body">' + body + '</div>';
        msgs.appendChild(div);
        msgs.scrollTop = msgs.scrollHeight;
    }
    function showThinking() {
        if (thinkingEl) return;
        const msgs = document.getElementById('msgs');
        thinkingEl = document.createElement('div');
        thinkingEl.className = 'msg bot';
        thinkingEl.innerHTML = '<div class="msg-speaker">boringbot</div><div class="thinking"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';
        msgs.appendChild(thinkingEl);
        msgs.scrollTop = msgs.scrollHeight;
    }

    // ── Markdown ──────────────────────────────────────────────────
    function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

    let _cbId = 0;
    function md(raw) {
        const chunks = raw.split(/(^```[\s\S]*?\n```$|^```[\s\S]*?```)/gm);
        return chunks.map(function (ch) {
            if (ch.startsWith('```')) {
                const lang = (ch.match(/^```(\S*)/) || [])[1] || '';
                const code = ch.replace(/^```\S*\n?/, '').replace(/\n?```$/, '');
                if (lang === 'diff') return renderDiff(code);
                const id = 'cb' + (++_cbId);
                return '<div class="cb-wrap"><div class="cb-bar"><span class="cb-lang">' + (lang || 'code') + '</span>'
                    + '<button class="btn-apply-code" data-code-id="' + id + '">⬆ 应用到文件</button></div>'
                    + '<pre><code id="' + id + '">' + esc(code) + '</code></pre></div>';
            }
            return ch
                .replace(/`([^`]+)`/g, function (_, c) { return '<code>' + esc(c) + '</code>'; })
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                .split('\n').map(function (l) { return esc(l); }).join('<br>');
        }).join('');
    }

    function renderDiff(code) {
        return '<div class="diff-block">' + code.split('\n').map(function (line) {
            if (line.startsWith('+++') || line.startsWith('---')) return '<span class="diff-header">' + esc(line) + '</span>';
            if (line.startsWith('@@')) return '<span class="diff-hunk">' + esc(line) + '</span>';
            if (line.startsWith('+')) return '<span class="diff-add">' + esc(line) + '</span>';
            if (line.startsWith('-')) return '<span class="diff-del">' + esc(line) + '</span>';
            return '<span class="diff-ctx">' + esc(line) + '</span>';
        }).join('') + '</div>';
    }

    function applyCode(id) {
        const el = document.getElementById(id);
        if (!el) return;
        vscode.postMessage({ type: 'applyEdit', code: el.textContent });
    }

    // ── Issue header ──────────────────────────────────────────────
    function showHeader(issue) {
        document.getElementById('issue-header').style.display = 'flex';
        document.getElementById('ih-key').textContent = issue.jira_key;
        document.getElementById('ih-title').textContent = issue.title || '';
        const sc = SC[issue.status] || '#9AA0AC';
        const b = document.getElementById('ih-badge');
        b.textContent = issue.status; b.style.background = sc + '22'; b.style.color = sc;

        // Root cause: full text, wrapping block
        var rcEl = document.getElementById('ih-root-cause');
        if (rcEl) {
            if (issue.root_cause) {
                rcEl.innerHTML = '<b>根因</b> ' + esc(issue.root_cause);
                rcEl.style.display = 'block';
            } else {
                rcEl.style.display = 'none';
            }
        }

        // File location chips (ctx-row)
        var ctx = document.getElementById('ctx-row'); ctx.innerHTML = '';
        if (issue.fix_code_location) {
            issue.fix_code_location.split(/[;,]/).map(function (s) { return s.trim(); }).filter(Boolean).forEach(function (loc) {
                var c = document.createElement('span');
                c.className = 'ctx-chip loc'; c.title = '跳转并查看修改建议: ' + loc; c.textContent = loc;
                c.addEventListener('click', function () {
                    vscode.postMessage({ type: 'openFile', file: loc });
                    sendAutoChat('请给出 ' + loc + ' 的具体修改建议');
                });
                ctx.appendChild(c);
            });
        }
        // Update selector bar
        var label = issue.jira_key + '  ' + (issue.title || '').slice(0, 28);
        document.getElementById('selector-text').textContent = label;
        document.getElementById('issue-selector-btn').classList.add('has-issue');
    }

    // ── History parser ────────────────────────────────────────────
    // Backend format: **speaker** `HH:MM`\n\ncontent\n\n---\n
    // Multiple stage files are concatenated; each starts with YAML frontmatter.
    function parseHistory(mdText) {
        if (!mdText || !mdText.trim()) return;
        var lines = mdText.split('\n');
        var cur = null, buf = [];
        var inFrontmatter = false;
        function flush() {
            if (!cur) return;
            var t = buf.join('\n').trim();
            if (t) appendMsg(cur.cls, cur.speaker, t);
            buf = []; cur = null;
        }
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (inFrontmatter) {
                if (line === '---') inFrontmatter = false;
                continue;
            }
            if (line === '---') {
                // Look ahead: if next non-empty line is a YAML key, this is a frontmatter start
                var nextNonEmpty = '';
                for (var j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                    if (lines[j].trim()) { nextNonEmpty = lines[j].trim(); break; }
                }
                if (/^\w[\w_-]*\s*:/.test(nextNonEmpty)) {
                    flush(); inFrontmatter = true;
                } else {
                    flush(); // message separator
                }
                continue;
            }
            // Message header: **speaker** `HH:MM`
            var m = line.match(/^\*\*(.+?)\*\*\s+`[\d:]+`/);
            if (m) {
                flush();
                var speaker = m[1];
                cur = { speaker: speaker, cls: speaker === 'boringbot' ? 'bot' : 'user' };
            } else if (cur) {
                buf.push(line);
            }
        }
        flush();
    }

    // ── Messages from extension host ──────────────────────────────
    window.addEventListener('message', function (e) {
        const msg = e.data;
        switch (msg.type) {
            case 'loading':
                document.getElementById('issue-header').style.display = 'none';
                document.getElementById('footer').style.display = 'none';
                document.getElementById('msgs').innerHTML = '';
                appendMsg('notice', '系统', '加载 ' + msg.key + '…');
                break;
            case 'loadIssue':
                document.getElementById('msgs').innerHTML = '';
                showHeader(msg.issue);
                document.getElementById('footer').style.display = 'flex';
                if (msg.historyMd) parseHistory(msg.historyMd);
                else appendMsg('bot', 'boringbot', '你好！我已加载 ' + msg.issue.jira_key + ' 的上下文，有什么想了解的？');
                document.getElementById('send-btn').disabled = false;
                break;
            case 'loadError':
                appendMsg('error', '系统', msg.text); break;
            case 'thinking': showThinking(); break;
            case 'reply':
                appendMsg(msg.speaker === 'error' ? 'error' : 'bot', msg.speaker, msg.text);
                document.getElementById('send-btn').disabled = false; break;
            case 'notice': appendMsg('notice', '系统', msg.text); break;
            case 'error': appendMsg('error', '系统', msg.text); break;
            case 'applyPending':
                document.getElementById('apply-bar-label').textContent =
                    '待确认对 ' + (msg.basename || '文件') + ' 的改动（右侧可编辑后再确认）';
                document.getElementById('apply-bar').style.display = 'flex';
                break;
            case 'applyDone':
                document.getElementById('apply-bar').style.display = 'none'; break;
            case 'recentIssues':
                var items = document.getElementById('issue-items');
                if (msg.issues && msg.issues.length && items) {
                    items.innerHTML = msg.issues.map(function (i) {
                        var k = esc(i.jira_key || i.key || '');
                        var sc = SC[i.status] || '#9AA0AC';
                        return '<div class="issue-item" data-key="' + k + '">'
                            + '<span class="ii-key">' + k + '</span>'
                            + '<span class="ii-title">' + esc((i.title || '').slice(0, 38)) + '</span>'
                            + '<span class="ii-status" style="background:' + sc + '22;color:' + sc + '">' + esc(i.status || '') + '</span>'
                            + '</div>';
                    }).join('');
                }
                break;
        }
    });

    // Signal ready to extension host
    vscode.postMessage({ type: 'ready' });
}());
