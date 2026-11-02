'use strict';
// Accessible in-page prompts. Fallback to native dialogs on older browsers.
// This is a presentation-only helper; server authorization and typed approvals remain unchanged.
const HomeAwayAdminDialog = (() => {
  async function ask(message, label) {
    const dialog = document.getElementById('admin-action-dialog');
    if (!dialog || typeof dialog.showModal !== 'function') {
      return label ? window.prompt(message) : window.confirm(message);
    }
    const text = document.getElementById('admin-action-message');
    const title = document.getElementById('admin-action-title');
    const inputLabel = document.getElementById('admin-action-input-label');
    const input = document.getElementById('admin-action-input');
    const cancel = document.getElementById('admin-action-cancel');
    const submit = document.getElementById('admin-action-submit');
    const form = dialog.querySelector('form');
    title.textContent = label ? 'Confirm identity' : 'Please confirm';
    text.textContent = message;
    inputLabel.hidden = !label;
    inputLabel.firstChild.textContent = label ? label + ' ' : '';
    input.value = '';
    submit.textContent = label ? 'Confirm' : 'Continue';
    return await new Promise(resolve => {
      let completed = false;
      const finish = value => {
        if (completed) return;
        completed = true;
        form.removeEventListener('submit', accept);
        cancel.removeEventListener('click', dismiss);
        dialog.removeEventListener('close', dismissed);
        if (dialog.open) dialog.close();
        resolve(value);
      };
      const accept = event => { event.preventDefault(); finish(label ? input.value : true); };
      const dismiss = () => finish(label ? null : false);
      const dismissed = () => finish(label ? null : false);
      form.addEventListener('submit', accept);
      cancel.addEventListener('click', dismiss);
      dialog.addEventListener('close', dismissed);
      dialog.showModal();
      (label ? input : cancel).focus();
    });
  }
  return {
    confirm: message => ask(message, null),
    input: message => ask(message, 'Type the exact value requested')
  };
})();
