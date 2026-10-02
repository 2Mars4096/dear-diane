import { BrowserWindow, Menu, type MenuItemConstructorOptions } from 'electron';
export function navigationDirection(key: string) {
  return key === 'BrowserBack' || key === 'browser-backward' ? -1 : key === 'BrowserForward' || key === 'browser-forward' ? 1 : 0;
}
export function registerNavigation(window: BrowserWindow) {
  const move = (direction: number) => { if (!window.isDestroyed()) window.webContents.send('navigation:move', direction); };
  window.on('app-command', (_event, command) => { const direction = navigationDirection(command); if (direction) move(direction); });
  window.on('swipe', (_event, direction) => { if (direction === 'left' || direction === 'right') move(direction === 'left' ? -1 : 1); });
  window.webContents.on('before-input-event', (event, input) => {
    const direction = navigationDirection(input.key);
    if (direction) { event.preventDefault(); if (input.type === 'keyDown' && !input.isAutoRepeat) move(direction); }
  });
  window.webContents.on('before-mouse-event', (event, input) => {
    const button = String(input.button);
    if (button === 'back' || button === 'forward') {
      event.preventDefault(); if (input.type === 'mouseUp') move(button === 'back' ? -1 : 1);
    }
  });
}
export function installNavigationMenu() {
  const navigate = (direction: number) => BrowserWindow.getFocusedWindow()?.webContents.send('navigation:move', direction);
  const menu: MenuItemConstructorOptions[] = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' as const }] : []),
    { role:'fileMenu' }, { role:'editMenu' }, { role:'viewMenu' },
    { label:'Go', submenu:[
      { label:'Back', accelerator:'CmdOrCtrl+[', click:() => navigate(-1) },
      { label:'Forward', accelerator:'CmdOrCtrl+]', click:() => navigate(1) },
    ] }, { role:'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(menu));
}
