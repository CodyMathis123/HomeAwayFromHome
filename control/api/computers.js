'use strict';
// Admin-only computer inventory UI. No browser access to host files or keys.
// Activation and restore must pass fresh SSH trust checks on the host writer.
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
  let auditBusy = false;
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
      (item.ssh_user || 'pcctl') + ':' + (item.ssh_port || 22);
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
    if (item.lifecyclePending) {
      card.append(make('p','account-detail',
        'Attention: a retirement recovery record exists for this active computer. ' +
        'Review before making further lifecycle changes.'));
    }
    const disable=make('button','danger','Disable computer controls');
    disable.type='button';
    disable.addEventListener('click',async()=>{
      const typed=prompt('Type "'+item.id+'" to DISABLE all new power commands for this computer:');
      if(typed!==item.id) return;
      if(!confirm('Disable '+item.id+'? Existing iOS Shortcuts for this ID will stop controlling it. ' +
        'Already-running commands cannot be cancelled. Its configuration will be saved for recovery.')) return;
      await mutate('/disable',{id:item.id,revision,confirm:typed},
        'Disabled '+item.id+'. It is no longer an active power-command target; its configuration was retained.');
    });
    card.append(disable);
    return card;
  }

  function retiredCard(record,inventoryRevision) {
    const item=record.host;
    const card=make('article','account-user');
    card.append(make('strong','',item.name),
      make('p','account-detail',formatDetail(item)),
      make('p','account-detail',
        (record.archived?'ARCHIVED':'DISABLED')+' · Recorded: '+record.retiredAt+
        ' · by '+record.retiredBy));
    if(record.cleanupPending) {
      card.append(make('p','account-detail',
        'Attention: this recovery record overlaps an active computer. '+
        'Its state is ambiguous; inspect the inventory and audit before modifying it.'));
      return card;
    }
    if(!record.archived) {
      const archive=make('button','','Archive (retain recovery record)');
      archive.type='button';
      archive.addEventListener('click',async()=>{
        const typed=prompt('Type "'+item.id+'" to archive this disabled computer:');
        if(typed!==item.id) return;
        if(!confirm('Archive '+item.id+'? No keys or historical data will be deleted.')) return;
        await mutate('/archive',{id:item.id,revision:record.revision,confirm:typed},
          'Archived '+item.id+'. Recovery details and audit history remain available.');
      });
      card.append(archive);
    }
    const restore=make('button','','Restore with SSH verification');
    restore.type='button';
    restore.addEventListener('click',async()=>{
      const typed=prompt('Type "'+item.id+'" to restore this computer to live power controls:');
      if(typed!==item.id) return;
      if(!confirm('Restore '+item.id+'? The Pi must verify its pinned SSH host key and '+
        'restricted Windows status command again. The original address and key must still match.')) return;
      await mutate('/restore',{id:item.id,revision:record.revision,
        inventoryRevision,confirm:typed},
        'Restored '+item.id+' after a new authenticated Windows SSH verification. '+
        'Review status before issuing power commands.');
    });
    card.append(restore);
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
      'On the Pi, compare the independently recorded fingerprint with the scanned host key using the preview command shown below. Pin only after the values match; then run Verify SSH and Activate. The server repeats SSH verification at activation.'
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
    const piNote = make('p','account-detail',
      'On the Pi, substitute the fingerprint read directly from Windows. ' +
      'Use --plan first, then repeat with --apply only after independently confirming the match.');
    const base = 'python3 scripts/pin-pc-hostkey.py --ip ' + item.address +
      ' --port ' + item.ssh_port +
      " --expected 'SHA256:REPLACE_WITH_WINDOWS_CONSOLE_VALUE'";
    const pinCommands=make('pre','computer-command',
      base + ' --plan' + '\n' + 'sudo ' + base + ' --apply');
    guide.append(steps,piNote,pinCommands,source);
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

  async function refreshAudit() {
    if (auditBusy) return;
    const details=$('computer-admin-audit');
    if (!details?.open) return;
    auditBusy=true;
    const status=$('computer-admin-audit-message');
    const events=$('computer-admin-audit-events');
    status.textContent='Loading recent administrative events…';
    try {
      const result=await api('/audit');
      if (!Array.isArray(result.events)) throw new Error('Unexpected audit format');
      const rows=result.events.map(event=>{
        const item=make('li','');
        // textContent is required: admin identity/targets are never HTML.
        item.textContent=event.time + ' · ' + event.actor + ' · ' +
          event.operation + ' · ' + event.target + ' · ' + event.outcome;
        return item;
      });
      events.replaceChildren(...rows);
      status.textContent=result.events.length ?
        'Showing '+result.events.length+' most recent events (newest first).' :
        'No computer-management activity recorded.';
    } catch(error) {
      status.textContent='Could not load audit history: '+error.message;
    } finally {
      auditBusy=false;
    }
  }

  async function refresh() {
    const button=$('computer-admin-refresh');
    if (busy) return false;
    button.disabled = true;
    try {
      const data=await api('');
      const active=data.active || [];
      const drafts=data.drafts || [];
      const retired=data.retired || [];
      $('computer-admin-active').replaceChildren(...active.map(item=>activeCard(item,data.revision)));
      if (!active.length) $('computer-admin-active').textContent='No active computers.';
      $('computer-admin-drafts').replaceChildren(...drafts.map(d=>draftCard(d,data.revision)));
      if (!drafts.length) $('computer-admin-drafts').textContent='No unconfigured drafts.';
      $('computer-admin-retired').replaceChildren(...retired.map(r=>retiredCard(r,data.revision)));
      if (!retired.length) $('computer-admin-retired').textContent='No disabled or archived computers.';
      if ($('computer-admin-audit')?.open) void refreshAudit();
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
    $('computer-admin-audit')?.addEventListener('toggle',event=>{
      if (event.currentTarget.open) void refreshAudit();
    });
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
