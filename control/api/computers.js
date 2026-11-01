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
  // Browser state is UX only. The host writer verifies SSH again on activate.
  const recentlyVerified = new Set();
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

  function draftCard(item, inventoryRevision) {
    const card = make('article','account-user');
    card.append(make('strong','',item.name),
      make('p','account-detail',formatDetail(item)),
      make('p','account-detail', item.cleanupPending ?
        'ALREADY ACTIVE · draft cleanup pending; discard this duplicate draft only' :
        'DRAFT · not active; no WOL, SSH commands, or browser power controls'));
    const guide = make('details','computer-setup');
    guide.append(make('summary','','Windows setup checklist (manual, no activation)'));
    const steps = make('ol','');
    const items = [
      'On the target Windows PC, confirm the correct Ethernet adapter and IPv4 settings:',
      'In an Administrator PowerShell window, check that Windows OpenSSH Server is installed:',
      'Use the reviewed HomeAway Windows setup script with -WhatIf first. Never rerun it blindly when a pcctl SSH Match block already exists.',
      'On Windows, record its SSH server host-key fingerprint. Compare that value with the fingerprint observed from the Pi over an independent, trusted channel.',
      'After explicit SSH host-key verification and restricted pcctl key setup, test an authenticated status command from the Pi. Do not use StrictHostKeyChecking=no.',
      'Run Verify SSH below after manually pinning the independently checked Windows host key. Only then activate; the server repeats the authenticated SSH test.'
    ];
    const commands = [
      'Get-NetAdapter; Get-NetIPAddress -AddressFamily IPv4',
      'Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0',
      '',
      'ssh-keygen.exe -lf C:\\ProgramData\\ssh\\ssh_host_ed25519_key.pub',
      '',
      ''
    ];
    for (let i=0;i<items.length;i++) {
      const li=make('li','',items[i]);
      if (commands[i]) li.append(make('pre','computer-command',commands[i]));
      steps.append(li);
    }
    const source = make('a','','Review Windows setup script and instructions');
    source.href='https://github.com/CodyMathis123/HomeAwayFromHome/blob/Production/docs/PC-CONTROL.md';
    source.target='_blank';
    source.rel='noopener noreferrer';
    guide.append(steps,source);
    card.append(guide);
    if (!item.cleanupPending) {
      const marker = item.id + ':' + item.revision;
      const note = make('p','account-detail',
        'Status: awaiting pinned host-key and authenticated Windows SSH status verification');
      const verify = make('button','','Verify SSH (non-destructive)');
      verify.type='button';
      const activate = make('button','','Activate verified computer');
      activate.type='button';
      activate.disabled = !recentlyVerified.has(marker);
      verify.addEventListener('click', async () => {
        if (busy) return;
        busy=true;
        verify.disabled=true;
        message('Checking pinned SSH host key and authenticated Windows status…');
        try {
          const result = await api('/verify',{id:item.id,revision:item.revision});
          if (!result.verified) {
            recentlyVerified.delete(marker);
            note.textContent='Not verified: ' + (result.code || 'SSH check failed');
            message('Activation blocked. Review Windows SSH setup and the pinned host-key fingerprint.');
            activate.disabled=true;
          } else {
            recentlyVerified.add(marker);
            note.textContent='Verified: trusted host key and restricted SSH status both passed';
            message('SSH identity verified. You can activate after confirming this exact computer.');
            activate.disabled=false;
          }
        } catch (err) {
          recentlyVerified.delete(marker);
          activate.disabled=true;
          message('SSH verification failed: ' + err.message);
        } finally {
          busy=false;
          verify.disabled=false;
        }
      });
      activate.addEventListener('click', async () => {
        if (!recentlyVerified.has(marker) || busy) return;
        const typed=prompt('Type the EXACT stable computer ID "' + item.id +
          '" to add it to the live controllable inventory:');
        if (typed !== item.id) return;
        if (!confirm('Activate ' + item.id + '? This adds the verified PC to real power controls and existing iOS Shortcut inventory.')) return;
        const done=await mutate('/activate',{
          id:item.id,revision:item.revision,inventoryRevision,confirm:typed
        },'Activated ' + item.id + '. The Pi reverified SSH trust before saving; verify normal status and controls.');
        if (done) recentlyVerified.delete(marker);
      });
      card.append(note,verify,activate);
    }
    const discard = make('button','danger',item.cleanupPending ?
      'Clear completed draft only' : 'Discard draft');
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
      $('computer-admin-drafts').replaceChildren(...drafts.map(d=>draftCard(d,data.revision)));
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
