import {useEffect, useRef, useState} from 'react';
import {Mic, PhoneOff} from 'lucide-react';
import {requestJson} from '../../lib/http';
import {PersonalVoiceSession, type VoiceProfile, type VoiceStatus} from '../../lib/personalVoice';

const VOICE = 'dan.personal.voice.v1';
type Profile = {id: VoiceProfile; name: string; description: string};
export default function VoiceControl({disabled, refresh, onError, onActive}: {disabled: boolean; refresh: () => void; onError: (message: string) => void; onActive: (active: boolean) => void}) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [localCaptions, setLocalCaptions] = useState(false);
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<VoiceStatus>('Connecting…');
  const [transcript, setTranscript] = useState('');
  const [profile, setProfile] = useState<VoiceProfile>(() => {
    try { const saved = localStorage.getItem(VOICE); if (['warm', 'bright', 'steady', 'composed'].includes(saved || '')) return saved as VoiceProfile; } catch { /* Default remains available. */ }
    return 'warm';
  });
  const session = useRef<PersonalVoiceSession | null>(null);
  const callbacks = useRef({refresh, onError, onActive});
  useEffect(() => { callbacks.current = {refresh, onError, onActive}; }, [refresh, onError, onActive]);
  useEffect(() => {
    let mounted = true;
    void requestJson<{enabled: boolean; local_captions?: boolean; profiles: Profile[]}>('/api/personal/voice').then(result => {
      if (mounted) { setEnabled(Boolean(result.enabled)); setLocalCaptions(Boolean(result.local_captions)); setProfiles(result.profiles || []); }
    }).catch(() => {});
    return () => { mounted = false; session.current?.end(); session.current = null; };
  }, []);
  const start = (selected = profile) => {
    session.current?.end();
    setOpen(true); setTranscript(''); setStatus('Connecting…'); callbacks.current.onError('');
    callbacks.current.onActive(true);
    const current = new PersonalVoiceSession(selected, {
      status: setStatus, transcript: setTranscript,
      refresh: () => callbacks.current.refresh(),
      error: message => callbacks.current.onError(message),
      ended: () => { if (session.current === current) { session.current = null; setOpen(false); callbacks.current.onActive(false); } },
    }, localCaptions);
    session.current = current; void current.start();
  };
  if (!enabled) return null;
  return open ? <div className="personal-voice-session" aria-label="Voice conversation">
    <span role="status">{status}</span>
    <select aria-label="Diane’s voice" disabled={status === 'Thinking…' || status === 'Hearing you…'} value={profile} title={profiles.find(item => item.id === profile)?.description} onChange={event => {
      const selected = event.target.value as VoiceProfile;
      setProfile(selected); try { localStorage.setItem(VOICE, selected); } catch { /* Session choice still applies. */ }
      start(selected);
    }}>{profiles.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select>
    <button type="button" aria-label="End voice conversation" title="End voice conversation" onClick={() => session.current?.end()}><PhoneOff size={18} /></button>
    {transcript && <p className="personal-voice-transcript" aria-label="Your voice transcript" aria-live="polite" aria-atomic="true">{transcript}</p>}
  </div> : <button type="button" disabled={disabled} aria-label="Start voice conversation" title="Start voice conversation" onClick={() => start()}><Mic size={18} /></button>;
}
