'use strict';
// Account administration is entirely server-enforced and opt-in. This module
// only displays capabilities and submits narrow operations; it cannot authorize.
const HomeAwayAccounts = (() => {
  let initialized = false;
  const $ = id => document.getElementById(id);
  const make = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const message = text => { $('accounts-message').textContent = text; };
  async function api(path, data) {
    const request = data === undefined ?
      {credentials:'same-origin',cache:'no-store'} :
      {method:'POST',credentials:'same-origin',
       headers:{'Content-Type':'application/json','X-HomeAway-Request':'1'},
       body:JSON.stringify(data)};
    const response = await fetch('/control/api/admin/users' + path,request);
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Account request failed');
    return result;
  }
  async function mutate(path, data, successMessage) {
    try {
      await api(path,data);
      const refreshed = await refresh();
      message(refreshed ?
        (successMessage || 'Account directory updated. Permissions are checked again on the next request.') :
        'Change succeeded, but the user list could not refresh. Use Refresh users to verify.');
      return true;
    } catch (err) {
      message('Change failed: ' + err.message + '. No success was confirmed; check the list before retrying.');
      return false;
    }
  }
  function renderUser(user) {
    const card = make('article','account-user');
    const title = make('div','top');
    title.append(make('strong','',user.name),make('code','account-id',user.id));
    card.append(title,make('p','account-detail',user.email));
    card.append(make('p','account-detail',user.role ?
      'HomeAway role: ' + user.role :
      'No HomeAway access · pending or revoked (password status unknown)'));
    if (user.id === 'admin') {
      card.append(make('p','account-detail','Directory bootstrap administrator · protected'));
      return card;
    }
    const control = make('div','account-buttons');
    const select = make('select','');
    for(const [value,label] of [
      ['viewers','Viewer · status only'],['operators','Operator · wake/sleep'],
      ['admins','Administrator · manage and power']]) {
      const option = make('option','',label);
      option.value = value;
      if (user.role === value) option.selected = true;
      select.append(option);
    }
    const save = make('button','','Set role');
    save.type='button';
    save.addEventListener('click', async () => {
      if (user.role === select.value) return;
      if (!confirm('Change HomeAway permissions for ' + user.id + '?')) return;
      await mutate('/set-role',{id:user.id,revision:user.revision,role:select.value});
    });
    const revoke = make('button','danger','Revoke HomeAway access');
    revoke.type='button';
    revoke.disabled = !user.homeawayAccess;
    revoke.addEventListener('click', async () => {
      if (!confirm('Revoke all HomeAway access for ' + user.id +
                   '? This does not disable their LDAP password or other applications.')) return;
      await mutate('/revoke',{id:user.id,revision:user.revision});
    });
    control.append(select,save,revoke);
    card.append(control);
    return card;
  }
  async function refresh() {
    const btn=$('accounts-refresh');
    btn.disabled=true;
    try {
      const result=await api('');
      $('accounts-users').replaceChildren(...result.users.map(renderUser));
      if (!result.users.length) $('accounts-users').textContent='No accounts found.';
      return true;
    } catch (err) {
      message('Account list unavailable: ' + err.message);
      return false;
    } finally { btn.disabled=false; }
  }
  function start(identity) {
    const section=$('accounts');
    if (!section || !identity?.capabilities?.manageUsers) {
      if (section) section.hidden=true;
      return;
    }
    section.hidden=false;
    if (initialized) return;
    initialized=true;
    $('accounts-refresh').addEventListener('click',refresh);
    $('accounts-add').addEventListener('submit',async event => {
      event.preventDefault();
      const form=event.currentTarget;
      const user=form.elements.namedItem('username').value.trim();
      const name=form.elements.namedItem('displayname').value.trim();
      const email=form.elements.namedItem('email').value.trim();
      if (!confirm('Create pending account ' + user +
                   '? No password or access will be provisioned yet.')) return;
      const created = await mutate('/create',{id:user,name,email},
        'Created pending user "' + user + '". Look for that username in the list below. ' +
        'No password or HomeAway access has been granted; set a password in LLDAP and assign a role here.');
      if (created) form.reset();
    });
    void refresh();
  }
  return {start,refresh};
})();
