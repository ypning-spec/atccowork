'use strict';
const vscode = require('vscode');
const { AcmsChatPanel, openFileAtLocation } = require('./acmsPanel');

function activate(context) {
    AcmsChatPanel.setContext(context);

    // 注册 WebviewViewProvider — 点击活动栏图标直接打开聊天面板
    const provider = new AcmsChatPanel();
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('acms.chat', provider, {
            webviewOptions: { retainContextWhenHidden: true }
        })
    );

    // acms.open：聚焦侧边栏视图（命令面板 / 快捷键）
    context.subscriptions.push(
        vscode.commands.registerCommand('acms.open', () => {
            vscode.commands.executeCommand('acms.chat.focus');
        })
    );

    // 关联 Issue（命令面板）
    context.subscriptions.push(
        vscode.commands.registerCommand('acms.setIssue', async () => {
            const key = await vscode.window.showInputBox({
                prompt: '输入 ACMS Issue Key',
                placeHolder: 'ACMS-46',
                validateInput: v => /^[A-Z]+-\d+$/.test(v.trim()) ? null : '格式应为 PROJECT-数字'
            });
            if (key) provider.openIssue(key.trim());
        })
    );

    // 记录代码变更
    context.subscriptions.push(
        vscode.commands.registerCommand('acms.recordChange', () => {
            provider._recordChange?.();
        })
    );

    // 深链接：vscode://autocharge.acms-cowork/open?key=ACMS-46&file=src/foo.py:32
    context.subscriptions.push(
        vscode.window.registerUriHandler({
            handleUri(uri) {
                const params = new URLSearchParams(uri.query);
                const key  = params.get('key');
                const file = params.get('file');
                if (key) provider.openIssue(key);
                if (file) openFileAtLocation(file);
            }
        })
    );
}

function deactivate() {}

module.exports = { activate, deactivate };
