// webviewSetup.js — setup page script
(function () {
    'use strict';
    const vscode = acquireVsCodeApi();
    function save() {
        vscode.postMessage({
            type: 'setConfig',
            serverUrl: document.getElementById('srv').value.trim(),
            token: document.getElementById('tok').value.trim()
        });
    }
    document.getElementById('save-btn').addEventListener('click', save);
    document.addEventListener('keydown', function (e) { if (e.key === 'Enter') save(); });
}());
