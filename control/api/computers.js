'use strict';
// Admin-only computer inventory UI. No browser access to host files or keys.
// Activation intentionally absent until Windows identity/SSH trust is verified.
const HomeAwayComputers = (() => {
  const $ = id => document.getElementById(id);
  const make = (tag, cls, content) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (content !== undefined) el.textContent = content;
    return el;
  };
  let started = false;
  let busy = false;
  const message = value => { $('computer-admin-message').textContent = value; };

  async function api(op, body) {
    const response = await fetch('/control/api/admin/computers' + op,
      body === undefined ? {credentials:'same-origin', cache:'no-store'} :
        {method:'POST', credentials:'same-origin',
         headers:{'Content-Type':'application/json','X-HomeAway-Request':'1'},
         body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Inventory request rejected');
    return data;
  }

  function formatDetail(item) {
    return item.id + ' · ' + item.address + ' · ' + item.mac + ' · ' +
      item.ssh_user + ':' + item.ssh_port;
  }

  function activeCard(item, revision) {
    const card = make('article','account-user');
    card.append(make('strong','',item.name),
      make('p','account-detail',formatDetail(item)));
    const rename = make('button','','Rename display name');
    rename.type = 'button';
    rename.addEventListener('click', async () => {
      const next = prompt('New display name (the computer ID will not change):',item.name);
      if (next === null || next.trim() === item.name) return;
      if (!next.trim()) { message('A display name is required.'); return; }
      if (!confirm('Rename ' + item.id + ' without changing its connection settings?')) return;
      await mutate('/rename',{id:item.id,name:next.trim(),revision},
        'Updated display name for ' + item.id + '. Existing Shortcut IDs are unchanged.');
    });
    card.append(rename);
    return card;
  }

  function draftCard(item) {
    const card = make('article','account-user');
    card.append(make('strong','',item.name),
      make('p','account-detail',formatDetail(item)),
      make('p','account-detail',
        'DRAFT · not active; no WOL, SSH commands, or browser power controls'));
    const discard = make('button','danger','Discard draft');
    discard.type='button';
    discard.addEventListener('click', async () => {
      if (!confirm('Discard unconfigured draft ' + item.id + '?')) return;
      await mutate('/draft-discard',{id:item.id,revision:item.revision},
        'Draft discarded. No active computer was changed.');
    });
    card.append(discard);
    return card;
  }

  async function refresh() {
    const button=$('computer-admin-refresh');
    if (busy) return false;
    button.disabled = true;
    try {
      const data=await api('');
      const active=data.active || [];
      const drafts=data.drafts || [];
      $('computer-admin-active').replaceChildren(...active.map(item=>activeCard(item,data.revision)));
      if (!active.length) $('computer-admin-active').textContent='No active computers.';
      $('computer-admin-drafts').replaceChildren(...drafts.map(draftCard));
      if (!drafts.length) $('computer-admin-drafts').textContent='No unconfigured drafts.';
      return true;
    } catch (err) {
      message('Cannot load computer inventory: ' + err.message);
      return false;
    } finally { button.disabled=false; }
  }

  async function mutate(op,payload,success) {
    if (busy) return false;
    busy = true;
    try {
      await api(op,payload);
      busy = false;
      if (await refresh()) message(success);
      else message('Change succeeded but the inventory list could not refresh.');
      return true;
    } catch (err) {
      busy = false;
      message('Change failed: ' + err.message + '. Refresh inventory before retrying.');
      return false;
    }
  }

  function start(identity) {
    const section=$('computer-admin');
    if (!section || !identity?.capabilities?.manageComputers) {
      if (section) section.hidden=true;
      return;
    }
    section.hidden=false;
    if (started) return;
    started=true;
    $('computer-admin-refresh').addEventListener('click',()=>{ void refresh(); });
    $('computer-admin-create').addEventListener('submit',async event=>{
      event.preventDefault();
      const form=event.currentTarget;
      const value=name=>form.elements.namedItem(name).value.trim();
      const draft={
        id:value('id'),name:value('name'),address:value('address'),
        mac:value('mac'),broadcast:value('broadcast'),ssh_user:value('ssh_user'),
        ssh_port:Number(value('ssh_port'))
      };
      if (!confirm('Save ' + draft.id + ' as an INACTIVE draft? No power commands will be enabled.')) return;
      if (await mutate('/draft-create',draft,
          'Draft created. It is NOT active; Windows setup and SSH verification come next.'))
        form.reset();
    });
    void refresh();
  }
  return {start,refresh};
})();
