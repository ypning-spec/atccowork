'use strict';
const vscode = require('vscode');
const { execSync } = require('child_process');
const http  = require('http');
const https = require('https');
const os    = require('os');
const path  = require('path');
const fs    = require('fs');

// ── WebviewViewProvider 实现 ──────────────────────────────────────

class AcmsChatPanel {
    static _context = null;
    static setContext(ctx) { AcmsChatPanel._context = ctx; }

    constructor() {
        this._view          = null;  // resolveWebviewView 后赋值
        this._issueKey      = null;
        this._issue         = null;
        this._autocleared   = false;
        this._webviewReady  = false;
        this._pendingKey    = null;
        this._pollTimer     = null;  // 定时轮询 conversation
        this._convMd        = '';    // 上次已渲染的 conversation markdown
        this._chatInFlight  = false; // 正在等待 bot 回复时暂停轮询
        this._pendingApply  = null;  // 等待用户确认的 AI 代码改动
    }

    // ── WebviewViewProvider 接口 ──────────────────────────────────

    resolveWebviewView(webviewView) {
        this._view = webviewView;
        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [vscode.Uri.joinPath(AcmsChatPanel._context.extensionUri, 'src')]
        };
        this._renderShell();

        webviewView.webview.onDidReceiveMessage(async msg => {
            switch (msg.type) {
                case 'setConfig':    await this._saveConfig(msg.serverUrl, msg.token); break;
                case 'ready':        await this._onWebviewReady(); break;
                case 'setIssue':     await this._openIssue(msg.key); break;
                case 'clearIssue':
                    this._stopPolling();
                    this._issueKey = null; this._issue = null;
                    this._autocleared = true;
                    this._renderShell(); break;
                case 'chat':         await this._handleChat(msg.text); break;
                case 'recordChange': await this._recordChange(); break;
                case 'openFile':     await openFileAtLocation(msg.file); break;
                case 'refresh':      await this._loadIssue(); break;
                case 'applyEdit':    await this._applyEdit(msg.code); break;
                case 'acceptApply':  await this._acceptApply(); break;
                case 'rejectApply':  this._rejectApply(); break;
                case 'fetchRecent':  await this._fetchRecentIssues(); break;
            }
        });

        // 面板隐藏时停轮询，重新显示时重启
        webviewView.onDidChangeVisibility(() => {
            if (webviewView.visible) {
                if (this._issueKey && this._webviewReady) this._startPolling();
            } else {
                this._stopPolling();
            }
        });
    }

    /** 聚焦侧边栏并加载指定 Issue */
    async openIssue(key) {
        await vscode.commands.executeCommand('acms.chat.focus');
        if (this._webviewReady) {
            await this._openIssue(key);
        } else {
            this._pendingKey = key;
        }
    }

    // ── 私有方法 ─────────────────────────────────────────────────

    _renderShell() {
        this._webviewReady = false;
        this._stopPolling();
        if (!this._view) return;
        const cfg     = vscode.workspace.getConfiguration('acms');
        const token   = cfg.get('token') || '';
        const webview = this._view.webview;
        const nonce   = getNonce();
        const csp     = webview.cspSource;
        if (token) {
            const scriptUri = webview.asWebviewUri(
                vscode.Uri.joinPath(AcmsChatPanel._context.extensionUri, 'src', 'webviewChat.js')
            );
            this._view.webview.html = buildChatShell(scriptUri, csp, nonce);
        } else {
            const scriptUri = webview.asWebviewUri(
                vscode.Uri.joinPath(AcmsChatPanel._context.extensionUri, 'src', 'webviewSetup.js')
            );
            this._view.webview.html = buildSetupHtml(
                cfg.get('serverUrl') || 'http://localhost:8000', scriptUri, csp, nonce
            );
        }
    }

    _send(data) { this._view?.webview.postMessage(data); }

    async _openIssue(key) {
        this._issueKey     = key;
        this._issue        = null;
        this._autocleared  = false;
        AcmsChatPanel._context?.globalState.update('lastIssueKey', key);
        await this._loadIssue();
    }

    async _loadIssue() {
        if (!this._issueKey) return;
        this._send({ type: 'loading', key: this._issueKey });
        try {
            const [issue, conv] = await Promise.all([
                this._get(`/issues/${this._issueKey}`),
                this._get(`/issues/${this._issueKey}/conversation`).catch(() => ({ raw_markdown: '' }))
            ]);
            this._issue = issue;
            this._view.title = `ACMS: ${issue.jira_key}`;
            this._convMd = conv.raw_markdown || '';
            this._send({ type: 'loadIssue', issue, historyMd: this._convMd });
            this._startPolling();

            // 自动在左侧编辑区打开第一个修复位置文件
            if (issue.fix_code_location) {
                const loc = issue.fix_code_location.split(/[;,]/)[0].trim();
                if (loc) await openFileAtLocation(loc);
            }
        } catch (e) {
            this._send({ type: 'loadError', text: e.message });
        }
    }

    async _handleChat(text) {
        if (!this._issueKey || !text.trim()) return;
        this._chatInFlight = true;
        this._send({ type: 'thinking' });
        try {
            const res = await this._post(`/chat/${this._issueKey}`, { text });
            this._send({ type: 'reply', speaker: 'boringbot', text: res.text });
            // 更新本地 convMd，避免轮询立即触发全量重渲染
            const conv = await this._get(`/issues/${this._issueKey}/conversation`).catch(() => null);
            if (conv?.raw_markdown) this._convMd = conv.raw_markdown;
        } catch (e) {
            this._send({ type: 'reply', speaker: 'error', text: `请求失败: ${e.message}` });
        } finally {
            this._chatInFlight = false;
        }
    }

    async _recordChange() {
        if (!this._issueKey) { this._send({ type: 'error', text: '请先关联一个 Issue' }); return; }
        const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!folder) { this._send({ type: 'error', text: '请在工程根目录打开 VS Code' }); return; }
        let diff = '', status = '';
        try {
            diff   = execSync('git diff HEAD', { cwd: folder, maxBuffer: 4 * 1024 * 1024 }).toString().trim();
            status = execSync('git status --short', { cwd: folder }).toString().trim();
        } catch {
            this._send({ type: 'error', text: 'git 命令失败，请确认当前目录是 git 仓库' }); return;
        }
        if (!diff && !status) { this._send({ type: 'notice', text: '未检测到代码变更（git diff HEAD 为空）' }); return; }
        this._send({ type: 'notice', text: '正在上传代码变更…' });
        try {
            await this._post(`/issues/${this._issueKey}/record-change`, { diff: diff.slice(0, 8000), status });
            this._send({ type: 'notice', text: `✅ 代码变更已记录到 ${this._issueKey}` });
        } catch (e) {
            this._send({ type: 'error', text: `记录失败 — ${e.message}` });
        }
    }

    async _saveConfig(serverUrl, token) {
        try {
            const cfg = vscode.workspace.getConfiguration('acms');
            await cfg.update('serverUrl', serverUrl, vscode.ConfigurationTarget.Global);
            await cfg.update('token',     token,     vscode.ConfigurationTarget.Global);
            this._renderShell();
        } catch (e) {
            vscode.window.showErrorMessage(`ACMS: 保存配置失败 — ${e.message}`);
        }
    }

    async _onWebviewReady() {
        this._webviewReady = true;
        const cfg   = vscode.workspace.getConfiguration('acms');
        const token = cfg.get('token') || '';
        if (!token) return;

        if (this._pendingKey) {
            const key = this._pendingKey;
            this._pendingKey = null;
            await this._openIssue(key);
        } else if (this._issueKey) {
            await this._loadIssue();
        } else if (!this._autocleared) {
            const lastKey = AcmsChatPanel._context?.globalState.get('lastIssueKey');
            if (lastKey) {
                await this._openIssue(lastKey);
            } else {
                await this._fetchRecentIssues();
            }
        } else {
            await this._fetchRecentIssues();
        }
    }

    async _fetchRecentIssues() {
        try {
            const data = await this._get('/issues?limit=8');
            const issues = Array.isArray(data) ? data : (data.items || data.issues || []);
            if (issues.length > 0) {
                this._send({ type: 'recentIssues', issues: issues.slice(0, 8) });
            }
        } catch (_) {}
    }

    _startPolling() {
        this._stopPolling();
        this._pollTimer = setInterval(() => this._pollConversation(), 20000); // 每 20s 轮询
    }

    _stopPolling() {
        if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
    }

    async _pollConversation() {
        if (!this._issueKey || !this._webviewReady || this._chatInFlight) return;
        try {
            const conv = await this._get(`/issues/${this._issueKey}/conversation`);
            const md = conv.raw_markdown || '';
            if (md !== this._convMd) {
                this._convMd = md;
                const issue = await this._get(`/issues/${this._issueKey}`);
                this._issue = issue;
                this._send({ type: 'loadIssue', issue, historyMd: md });
            }
        } catch (_) {}
    }

    async _applyEdit(code) {
        // Clean up any previous pending apply
        if (this._pendingApply) {
            try { fs.unlinkSync(this._pendingApply.tmpPath); } catch {}
            this._pendingApply = null;
            this._send({ type: 'applyDone' });
        }
        let targetUri = null;
        const activeEditor = vscode.window.activeTextEditor;
        if (activeEditor && activeEditor.document.uri.scheme === 'file') {
            targetUri = activeEditor.document.uri;
        }
        if (!targetUri) {
            const allFiles = await vscode.workspace.findFiles(
                '**/*.*', '{**/node_modules/**,**/.git/**,**/.vsix}', 500
            );
            const items = allFiles
                .map(uri => ({ label: vscode.workspace.asRelativePath(uri), uri }))
                .sort((a, b) => a.label.localeCompare(b.label));
            const picked = await vscode.window.showQuickPick(items, {
                placeHolder: '选择要应用 AI 改动的目标文件', matchOnDescription: true
            });
            if (!picked) return;
            targetUri = picked.uri;
        }
        const targetPath   = targetUri.fsPath;
        const basename     = path.basename(targetPath);
        const originalCode = fs.existsSync(targetPath) ? fs.readFileSync(targetPath, 'utf8') : '';
        const tmpPath      = path.join(os.tmpdir(), `acms_${Date.now()}_${basename}`);
        fs.writeFileSync(tmpPath, code, 'utf8');
        await vscode.commands.executeCommand(
            'vscode.diff', targetUri, vscode.Uri.file(tmpPath),
            `${basename}  ←  AI 建议（右侧可手动调整）`,
            { viewColumn: vscode.ViewColumn.Beside, preview: true }
        );
        this._pendingApply = { targetUri, targetPath, basename, tmpPath, originalCode };
        this._send({ type: 'applyPending', basename });
    }

    async _acceptApply() {
        const p = this._pendingApply;
        if (!p) return;
        this._pendingApply = null;
        try {
            const finalCode = fs.existsSync(p.tmpPath)
                ? fs.readFileSync(p.tmpPath, 'utf8') : p.originalCode;
            fs.writeFileSync(p.targetPath, finalCode, 'utf8');
            await vscode.commands.executeCommand('workbench.action.files.revert', p.targetUri);
            // Record diff to backend conversations
            if (this._issueKey && finalCode !== p.originalCode) {
                const origTmp = path.join(os.tmpdir(), `acms_orig_${Date.now()}`);
                const newTmp  = path.join(os.tmpdir(), `acms_new_${Date.now()}`);
                try {
                    fs.writeFileSync(origTmp, p.originalCode, 'utf8');
                    fs.writeFileSync(newTmp,  finalCode,      'utf8');
                    let diffText = '';
                    try { diffText = execSync(`diff -u "${origTmp}" "${newTmp}"`, { encoding: 'utf8' }); }
                    catch (de) { diffText = typeof de.stdout === 'string' ? de.stdout : ''; }
                    if (diffText.trim()) {
                        await this._post(`/issues/${this._issueKey}/record-change`, {
                            diff: diffText.slice(0, 8000),
                            status: `Applied AI edit to ${p.basename}`
                        });
                    }
                } catch (_) {}
                finally {
                    try { fs.unlinkSync(origTmp); } catch {}
                    try { fs.unlinkSync(newTmp);  } catch {}
                }
            }
            this._send({ type: 'notice', text: `✅ 已更新 ${p.basename}` });
        } catch (e) {
            this._send({ type: 'error', text: `应用失败: ${e.message}` });
        } finally {
            try { fs.unlinkSync(p.tmpPath); } catch {}
            this._send({ type: 'applyDone' });
        }
    }

    _rejectApply() {
        const p = this._pendingApply;
        if (!p) return;
        this._pendingApply = null;
        try { fs.unlinkSync(p.tmpPath); } catch {}
        this._send({ type: 'notice', text: `已拒绝对 ${p.basename} 的改动` });
        this._send({ type: 'applyDone' });
    }

    _cfg() {
        const cfg = vscode.workspace.getConfiguration('acms');
        return { serverUrl: cfg.get('serverUrl') || 'http://localhost:8000', token: cfg.get('token') || '' };
    }
    _get(path)       { const { serverUrl, token } = this._cfg(); return request('GET',  serverUrl + path, null,  token); }
    _post(path, body){ const { serverUrl, token } = this._cfg(); return request('POST', serverUrl + path, body,  token); }
}

// ── 文件跳转（多策略） ─────────────────────────────────────────────

async function tryFindFile(relPath) {
    if (!relPath) return null;
    for (const folder of vscode.workspace.workspaceFolders || []) {
        const uri = vscode.Uri.joinPath(folder.uri, relPath);
        try { await vscode.workspace.fs.stat(uri); return uri; } catch {}
    }
    const results = await vscode.workspace.findFiles(
        `**/${relPath.replace(/^\//, '')}`, '**/node_modules/**', 1
    );
    return results.length ? results[0] : null;
}

async function openFileAtLocation(fileAndLine) {
    const m = fileAndLine.match(/^(.+?):(?:L?(\d+)(?:-L?\d+)?)?$/);
    const filePath = m ? m[1] : fileAndLine;
    const line     = m && m[2] ? Math.max(0, parseInt(m[2]) - 1) : 0;

    const cfg = vscode.workspace.getConfiguration('acms');
    const prefixStrip = (cfg.get('pathPrefixStrip') || '').trim();
    const stripped = prefixStrip && filePath.startsWith(prefixStrip)
        ? filePath.slice(prefixStrip.length) : filePath;

    let uri = await tryFindFile(stripped);

    if (!uri) {
        const parts = stripped.replace(/^\//, '').split('/');
        for (let i = 1; i < parts.length && !uri; i++) {
            uri = await tryFindFile(parts.slice(i).join('/'));
        }
    }

    if (!uri) {
        const basename = path.basename(filePath);
        const results = await vscode.workspace.findFiles(`**/${basename}`, '**/node_modules/**', 20);
        if (results.length === 0) {
            const choice = await vscode.window.showWarningMessage(
                `ACMS: 找不到文件 ${filePath}`, '在工作区搜索…'
            );
            if (choice === '在工作区搜索…') {
                await vscode.commands.executeCommand('workbench.action.quickOpen', basename);
            }
            return;
        }
        if (results.length === 1) {
            uri = results[0];
        } else {
            const items = results.map(u => ({
                label: vscode.workspace.asRelativePath(u),
                description: u.fsPath,
                uri: u
            }));
            const picked = await vscode.window.showQuickPick(items, {
                placeHolder: `找到多个 "${basename}"，请选择目标文件`
            });
            if (!picked) return;
            uri = picked.uri;
        }
    }

    const doc    = await vscode.workspace.openTextDocument(uri);
    const editor = await vscode.window.showTextDocument(doc, {
        viewColumn: vscode.ViewColumn.Beside, preserveFocus: true
    });
    const pos = new vscode.Position(line, 0);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
}

// ── HTTP 请求 ──────────────────────────────────────────────────────

function request(method, url, body, token) {
    return new Promise((resolve, reject) => {
        let parsed;
        try { parsed = new URL(url); } catch (e) { return reject(new Error('无效的服务器地址: ' + url)); }
        const options = {
            hostname: parsed.hostname,
            port:     parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
            path:     parsed.pathname + parsed.search,
            method,
            headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }
        };
        const payload = body ? JSON.stringify(body) : null;
        if (payload) options.headers['Content-Length'] = Buffer.byteLength(payload);
        const mod = parsed.protocol === 'https:' ? https : http;
        const req = mod.request(options, res => {
            let data = '';
            res.on('data', d => data += d);
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    try { resolve(JSON.parse(data)); } catch { resolve(data); }
                } else {
                    reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 200)}`));
                }
            });
        });
        req.on('error', reject);
        if (payload) req.write(payload);
        req.end();
    });
}

// ── Helpers ────────────────────────────────────────────────────────

function getNonce() {
    let text = '';
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) text += chars.charAt(Math.floor(Math.random() * chars.length));
    return text;
}

function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── HTML ──────────────────────────────────────────────────────────

function buildSetupHtml(serverUrl, scriptUri, cspSource, nonce) {
    return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src ${cspSource} 'nonce-${nonce}'; style-src 'unsafe-inline';">
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:var(--vscode-font-family);font-size:13px;color:var(--vscode-foreground);
  background:var(--vscode-editor-background);display:flex;align-items:center;justify-content:center;
  height:100vh;padding:24px;}
.card{width:360px;display:flex;flex-direction:column;gap:10px;}
h2{font-size:16px;font-weight:600;margin-bottom:4px;}
.hint{font-size:12px;color:var(--vscode-descriptionForeground);}
label{font-size:11px;color:var(--vscode-descriptionForeground);margin-top:4px;}
input{width:100%;background:var(--vscode-input-background);border:1px solid var(--vscode-input-border,#3c3c3c);
  color:var(--vscode-input-foreground);padding:6px 10px;border-radius:4px;font-size:13px;font-family:inherit;}
input:focus{outline:1px solid var(--vscode-focusBorder,#007fd4);border-color:transparent;}
.btn{padding:8px 0;border-radius:4px;border:none;cursor:pointer;font-size:13px;font-family:inherit;
  background:var(--vscode-button-background);color:var(--vscode-button-foreground);width:100%;margin-top:4px;}
.btn:hover{opacity:.9;}
</style></head><body>
<div class="card">
  <h2>ACMS Cowork</h2>
  <p class="hint">配置服务器地址和 Token 以开始使用</p>
  <label>服务器地址</label>
  <input id="srv" type="text" value="${esc(serverUrl)}" placeholder="http://localhost:8000"/>
  <label>Token <span style="opacity:.6">（ACMS Web → 右上角「复制 Token」）</span></label>
  <input id="tok" type="password" placeholder="粘贴 JWT Token…"/>
  <button class="btn" id="save-btn">保存配置</button>
</div>
<script src="${scriptUri}" nonce="${nonce}"></script>
</body></html>`;
}

function buildChatShell(scriptUri, cspSource, nonce) {
    return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src ${cspSource} 'nonce-${nonce}'; style-src 'unsafe-inline';">
<style>
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;overflow:hidden;}
body{
  display:flex;flex-direction:column;
  font-family:var(--vscode-font-family);font-size:13px;
  color:var(--vscode-foreground);
  background:var(--vscode-editor-background);
}

/* ── selector bar (always visible, like "Past Conversations") ── */
#selector-bar{
  display:flex;align-items:center;gap:4px;
  padding:6px 8px;flex-shrink:0;
  border-bottom:1px solid var(--vscode-editorGroup-border,#333);
}
#issue-selector-btn{
  flex:1;display:flex;align-items:center;gap:6px;
  background:var(--vscode-input-background,#3c3c3c);
  border:1px solid var(--vscode-input-border,#555);
  color:var(--vscode-foreground);
  padding:5px 10px;border-radius:4px;cursor:pointer;
  font-size:12px;font-family:inherit;text-align:left;
  overflow:hidden;
}
#issue-selector-btn:hover{border-color:var(--vscode-focusBorder,#007fd4);}
#issue-selector-btn.has-issue{
  border-color:var(--vscode-focusBorder,#007fd4);
  background:var(--vscode-input-background,#3c3c3c);
}
#selector-text{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.selector-arrow{font-size:10px;opacity:.6;flex-shrink:0;}
#new-issue-btn{
  background:transparent;border:1px solid var(--vscode-input-border,#555);
  color:var(--vscode-descriptionForeground);
  padding:5px 9px;border-radius:4px;cursor:pointer;font-size:14px;flex-shrink:0;
}
#new-issue-btn:hover{color:var(--vscode-foreground);border-color:var(--vscode-focusBorder,#007fd4);}

/* ── new issue input row ── */
#new-issue-row{
  display:none;align-items:center;gap:6px;
  padding:6px 8px;flex-shrink:0;
  border-bottom:1px solid var(--vscode-editorGroup-border,#333);
  background:var(--vscode-editorWidget-background,#252526);
}
#ik{
  flex:1;background:var(--vscode-input-background);
  border:1px solid var(--vscode-input-border,#3c3c3c);
  color:var(--vscode-input-foreground);
  padding:5px 10px;border-radius:4px;font-size:12px;font-family:inherit;
}
#ik:focus{outline:1px solid var(--vscode-focusBorder,#007fd4);border-color:transparent;}
#set-issue-btn{
  padding:5px 12px;border-radius:4px;border:none;cursor:pointer;
  font-size:12px;font-family:inherit;
  background:var(--vscode-button-background);color:var(--vscode-button-foreground);
  flex-shrink:0;
}
#set-issue-btn:hover{opacity:.9;}

/* ── issue dropdown overlay ── */
#issue-dropdown{
  display:none;position:absolute;
  left:8px;right:8px;top:40px;
  background:var(--vscode-editorWidget-background,#252526);
  border:1px solid var(--vscode-editorGroup-border,#454545);
  border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.4);
  z-index:100;overflow:hidden;max-height:320px;overflow-y:auto;
}
.dropdown-hint{font-size:11px;color:var(--vscode-descriptionForeground);
  padding:8px 12px 4px;border-bottom:1px solid var(--vscode-editorGroup-border,#333);}
.issue-item{
  display:flex;align-items:center;gap:8px;
  padding:8px 12px;cursor:pointer;
}
.issue-item:hover{background:var(--vscode-list-hoverBackground,#2a2d2e);}
.ii-key{font-weight:700;font-size:12px;font-family:var(--vscode-editor-font-family,monospace);
  white-space:nowrap;flex-shrink:0;}
.ii-title{flex:1;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  color:var(--vscode-descriptionForeground);}
.ii-status{font-size:11px;padding:1px 7px;border-radius:8px;white-space:nowrap;flex-shrink:0;}

/* ── main area ── */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden;position:relative;}

/* ── issue header (hidden until issue loaded) ── */
#issue-header{
  display:none;flex-direction:column;gap:4px;
  padding:10px 12px 8px;flex-shrink:0;
  border-bottom:1px solid var(--vscode-editorGroup-border,#333);
}
.ih-row{display:flex;align-items:center;gap:8px;}
.ik{font-weight:700;font-size:14px;}
.badge{padding:2px 9px;border-radius:10px;font-size:11px;white-space:nowrap;}
.ih-actions{margin-left:auto;display:flex;gap:4px;}
.btn-icon{background:transparent;border:1px solid transparent;color:var(--vscode-descriptionForeground);
  padding:2px 6px;font-size:13px;cursor:pointer;border-radius:4px;}
.btn-icon:hover{background:var(--vscode-toolbar-hoverBackground,rgba(255,255,255,.08));border-color:var(--vscode-editorGroup-border,#444);}
.ih-title{font-size:12px;color:var(--vscode-descriptionForeground);}
#ih-root-cause{
  display:none;font-size:11px;line-height:1.6;word-break:break-word;
  background:var(--vscode-textBlockQuote-background,#2a2a2a);
  border-left:2px solid var(--vscode-descriptionForeground,#666);
  border-radius:3px;padding:4px 8px;margin-top:4px;
  color:var(--vscode-foreground);
}
#ih-root-cause b{font-weight:600;color:var(--vscode-descriptionForeground);}
.ctx-row{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;}
.ctx-chip{font-size:11px;background:var(--vscode-textBlockQuote-background,#2a2a2a);
  border-radius:4px;padding:2px 8px;max-width:480px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.ctx-chip b{font-weight:600;}
.ctx-chip.loc{cursor:pointer;border-left:2px solid var(--vscode-focusBorder,#007fd4);}
.ctx-chip.loc:hover{opacity:.8;}

/* ── messages ── */
#msgs{
  flex:1;overflow-y:auto;padding:16px 14px;
  display:flex;flex-direction:column;gap:14px;
}
.msg{display:flex;flex-direction:column;gap:4px;}
.msg-speaker{font-size:11px;color:var(--vscode-descriptionForeground);text-transform:uppercase;letter-spacing:.5px;}
.msg-body{font-size:13px;line-height:1.7;word-break:break-word;}
.msg.user{align-items:flex-end;}
.msg.user .msg-body{
  background:var(--vscode-button-background);color:var(--vscode-button-foreground);
  border-radius:12px 12px 2px 12px;padding:7px 12px;
}
.msg.bot .msg-body{color:var(--vscode-foreground);}
.msg.notice .msg-body{
  font-size:12px;color:var(--vscode-descriptionForeground);
  background:var(--vscode-textBlockQuote-background,#2a2a2a);
  border-radius:6px;padding:4px 10px;border-left:3px solid var(--vscode-editorGroup-border,#444);
}
.msg.error .msg-body{
  font-size:12px;color:var(--vscode-errorForeground,#f48771);
  background:rgba(244,135,113,.08);border-radius:6px;padding:4px 10px;
  border-left:3px solid var(--vscode-errorForeground,#f48771);
}
.thinking{display:flex;gap:5px;align-items:center;padding:4px 0;}
.dot{width:7px;height:7px;border-radius:50%;background:var(--vscode-descriptionForeground);
  opacity:.3;animation:blink 1.4s infinite;}
.dot:nth-child(2){animation-delay:.2s;}.dot:nth-child(3){animation-delay:.4s;}
@keyframes blink{0%,80%,100%{opacity:.15;}40%{opacity:.9;}}

/* ── markdown ── */
.msg-body strong{font-weight:600;}
.msg-body code{font-family:var(--vscode-editor-font-family,monospace);font-size:12px;
  background:var(--vscode-textBlockQuote-background,#2d2d2d);padding:1px 5px;border-radius:3px;}
.cb-wrap{margin:6px 0;border-radius:6px;overflow:hidden;
  border:1px solid var(--vscode-editorGroup-border,#333);}
.cb-bar{display:flex;align-items:center;justify-content:space-between;padding:4px 12px;
  background:var(--vscode-editorGroupHeader-tabsBackground,#252526);font-size:11px;}
.cb-lang{color:var(--vscode-descriptionForeground);text-transform:lowercase;font-family:var(--vscode-editor-font-family,monospace);}
.btn-apply-code{font-size:11px;padding:2px 8px;border-radius:3px;border:none;cursor:pointer;
  font-family:inherit;background:var(--vscode-button-background);color:var(--vscode-button-foreground);}
.btn-apply-code:hover{opacity:.85;}
.cb-wrap pre{margin:0;border-radius:0;border:none;
  background:var(--vscode-textCodeBlock-background,#1e1e1e);
  padding:12px 14px;overflow-x:auto;font-size:12px;}
.cb-wrap pre code{background:none;padding:0;}
.diff-block{font-family:var(--vscode-editor-font-family,monospace);font-size:12px;
  border-radius:6px;overflow:hidden;margin:6px 0;
  border:1px solid var(--vscode-editorGroup-border,#333);}
.diff-add{background:rgba(70,149,74,.18);color:#b5e4b7;padding:0 12px;white-space:pre-wrap;display:block;}
.diff-del{background:rgba(218,54,51,.18);color:#f7a8a8;padding:0 12px;white-space:pre-wrap;display:block;}
.diff-hunk{background:rgba(79,142,247,.12);color:#88b8f7;padding:0 12px;white-space:pre-wrap;display:block;}
.diff-header{background:var(--vscode-textBlockQuote-background,#2a2a2a);padding:0 12px;
  white-space:pre-wrap;display:block;color:var(--vscode-descriptionForeground);}
.diff-ctx{padding:0 12px;white-space:pre-wrap;display:block;color:var(--vscode-foreground);}

/* ── footer (hidden until issue loaded) ── */
#footer{
  display:none;flex-direction:column;
  border-top:1px solid var(--vscode-editorGroup-border,#333);
  padding:8px 10px;flex-shrink:0;gap:6px;
}
.foot-top{display:flex;justify-content:flex-end;}
.btn-record{font-size:11px;padding:3px 10px;border-radius:4px;border:none;cursor:pointer;font-family:inherit;
  background:transparent;color:var(--vscode-descriptionForeground);
  border:1px solid var(--vscode-editorGroup-border,#444);}
.btn-record:hover{color:var(--vscode-foreground);}
.input-row{display:flex;gap:8px;align-items:flex-end;}
#inp{
  flex:1;resize:none;min-height:36px;max-height:160px;
  background:var(--vscode-input-background);
  border:1px solid var(--vscode-input-border,#3c3c3c);
  color:var(--vscode-input-foreground);
  padding:7px 10px;border-radius:6px;font-size:13px;font-family:inherit;line-height:1.5;
}
#inp:focus{outline:1px solid var(--vscode-focusBorder,#007fd4);border-color:transparent;}
#send-btn{padding:7px 14px;border-radius:6px;border:none;cursor:pointer;font-size:13px;font-family:inherit;
  background:var(--vscode-button-background);color:var(--vscode-button-foreground);flex-shrink:0;}
#send-btn:hover{opacity:.9;}
#send-btn:disabled{opacity:.35;cursor:default;}
/* ── apply bar ── */
#apply-bar{
  display:none;align-items:center;justify-content:space-between;gap:8px;
  padding:7px 12px;flex-shrink:0;
  background:rgba(79,142,247,.1);
  border-bottom:1px solid var(--vscode-focusBorder,#007fd4);
}
#apply-bar-label{font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.apply-bar-btns{display:flex;gap:6px;flex-shrink:0;}
#apply-accept-btn{font-size:11px;padding:3px 10px;border-radius:4px;border:none;cursor:pointer;font-family:inherit;
  background:var(--vscode-button-background);color:var(--vscode-button-foreground);}
#apply-reject-btn{font-size:11px;padding:3px 10px;border-radius:4px;cursor:pointer;font-family:inherit;
  background:transparent;border:1px solid var(--vscode-editorGroup-border,#444);color:var(--vscode-descriptionForeground);}
#apply-accept-btn:hover{opacity:.85;}
#apply-reject-btn:hover{color:var(--vscode-foreground);}
</style></head><body>

<!-- ── Top selector bar (always visible) ── -->
<div id="selector-bar">
  <button id="issue-selector-btn">
    <span id="selector-text">选择 Issue…</span>
    <span class="selector-arrow">▾</span>
  </button>
  <button id="new-issue-btn" title="手动输入 Issue Key">+</button>
</div>

<!-- ── New issue key input row (hidden by default) ── -->
<div id="new-issue-row">
  <input id="ik" type="text" placeholder="ACMS-46" autocomplete="off"/>
  <button id="set-issue-btn">关联</button>
</div>

<!-- ── Dropdown overlay ── -->
<div id="issue-dropdown">
  <p class="dropdown-hint">最近的 Issues</p>
  <div id="issue-items"></div>
</div>

<!-- ── Main content area ── -->
<div id="main">
  <!-- Issue header (shown after loading) -->
  <div id="issue-header">
    <div class="ih-row">
      <span class="ik" id="ih-key"></span>
      <span class="badge" id="ih-badge"></span>
      <div class="ih-actions">
        <button class="btn-icon" id="btn-refresh" title="刷新">↺</button>
        <button class="btn-icon" id="btn-clear" title="切换 Issue">✕</button>
      </div>
    </div>
    <div class="ih-title" id="ih-title"></div>
    <div id="ih-root-cause"></div>
    <div class="ctx-row" id="ctx-row"></div>
  </div>

  <!-- Apply bar (persistent accept/reject for pending AI edits) -->
  <div id="apply-bar">
    <span id="apply-bar-label"></span>
    <div class="apply-bar-btns">
      <button id="apply-accept-btn">✓ 接受</button>
      <button id="apply-reject-btn">✗ 拒绝</button>
    </div>
  </div>

  <!-- Messages -->
  <div id="msgs"></div>
</div>

<!-- ── Footer (shown after issue loaded) ── -->
<div id="footer">
  <div class="foot-top">
    <button class="btn-record" id="btn-record" title="执行 git diff HEAD 并上传">⬆ 记录代码变更</button>
  </div>
  <div class="input-row">
    <textarea id="inp" rows="1" placeholder="问 AI 关于这个 Issue 的问题…"></textarea>
    <button id="send-btn" disabled>发送</button>
  </div>
</div>

<script src="${scriptUri}" nonce="${nonce}"></script>
</body></html>`;
}

module.exports = { AcmsChatPanel, openFileAtLocation };
