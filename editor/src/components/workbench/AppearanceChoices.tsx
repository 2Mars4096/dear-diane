import { useSettingsStore } from '../../store/useSettingsStore';
import { WORKBENCH_PALETTES, workbenchPalette } from '../../lib/workbenchPalette';
import { WORKBENCH_FONTS, workbenchFont } from '../../lib/workbenchFonts';

export function AppearanceChoices() {
  const settings = useSettingsStore();
  const current = workbenchPalette(settings.workbenchColorScheme);
  const set = settings.textFont === settings.headingFont && settings.textFont === settings.highlightFont ? settings.textFont : 'custom';
  return <>
    <div className="wb-appearance-preview" aria-label="Live appearance preview">
      <div className="wb-appearance-preview-sidebar"><span>Diane</span><span>Research notes</span><span>New conversation</span></div>
      <div className="wb-appearance-preview-content"><small>YOUR WORKSPACE</small><h3>Room for the next idea.</h3><p>Gather your research, follow a thought, and make something worth keeping.</p><blockquote>A passage worth coming back to.</blockquote><div className="wb-appearance-preview-composer">Ask Diane anything<span aria-hidden="true">↑</span></div></div>
    </div>
    <label className="wb-preference-row"><span className="wb-preference-copy"><span>Color scheme</span><small>Preview changes above as you choose.</small></span><select aria-label="Color scheme" value={current.id} onChange={event => settings.updateSetting('workbenchColorScheme', workbenchPalette(event.target.value).id)}>{WORKBENCH_PALETTES.map(palette => <option key={palette.id} value={palette.id}>{palette.label}</option>)}</select></label>
    <label className="wb-preference-row"><span className="wb-preference-copy"><span>Font set</span><small>A starting font for all three roles.</small></span><select aria-label="Font set" value={set} onChange={event => { const id = workbenchFont(event.target.value).id; settings.updateSetting('textFont', id); settings.updateSetting('headingFont', id); settings.updateSetting('highlightFont', id); }}>
      {set === 'custom' && <option value="custom">Custom mix</option>}{WORKBENCH_FONTS.map(font => <option key={font.id} value={font.id}>{font.label}</option>)}
    </select></label>
    {([
      ['textFont', 'Normal text'], ['headingFont', 'Headings & emphasis'], ['highlightFont', 'Highlighted quotes'],
    ] as const).map(([key, label]) => <label key={key} className="wb-preference-row"><span className="wb-preference-copy"><span>{label}</span></span><select aria-label={`${label} font`} value={settings[key]} onChange={event => settings.updateSetting(key, workbenchFont(event.target.value).id)}>{WORKBENCH_FONTS.map(font => <option value={font.id} key={font.id}>{font.label}</option>)}</select></label>)}
    <p className="wb-appearance-help">Orbitron and Monoton work best for short headings. Quote fonts apply in Notes and References; original PDF and EPUB typography stays with the document.</p>
  </>;
}
