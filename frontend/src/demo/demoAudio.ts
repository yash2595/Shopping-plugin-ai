export type DemoAudioClip =
  | 'userFindMonuments'
  | 'saarthiSearching'
  | 'saarthiResults'
  | 'saarthiAskSite'
  | 'userSecondResult'
  | 'saarthiOpening'
  | 'saarthiComplete'

const AUDIO_ASSETS: Record<DemoAudioClip, string> = {
  userFindMonuments: './audio/user-find-monuments.mp3',
  saarthiSearching: './audio/saarthi-searching.mp3',
  saarthiResults: './audio/saarthi-results.mp3',
  saarthiAskSite: './audio/saarthi-ask-site.mp3',
  userSecondResult: './audio/user-second-result.mp3',
  saarthiOpening: './audio/saarthi-opening.mp3',
  saarthiComplete: './audio/saarthi-complete.mp3',
}

let activeAudio: HTMLAudioElement | null = null
const isSaarthiClip = (clip: DemoAudioClip) => clip.startsWith('saarthi')
const clipName = (clip: DemoAudioClip) => AUDIO_ASSETS[clip].split('/').pop() || clip

export function stopDemoAudio() {
  window.electronAPI?.setOrbSpeaking(false)
  if (!activeAudio) return
  activeAudio.onended = null
  activeAudio.onerror = null
  activeAudio.pause()
  activeAudio.currentTime = 0
}

export function playDemoAudio(clip: DemoAudioClip): Promise<void> {
  stopDemoAudio()
  window.electronAPI?.setOrbSpeaking(isSaarthiClip(clip))
  const audio = activeAudio || new Audio()
  activeAudio = audio
  audio.src = AUDIO_ASSETS[clip]
  audio.preload = 'auto'
  console.info(`[DemoAudio] Playing ${clipName(clip)}`)

  return new Promise((resolve) => {
    let settled = false
    const finish = (reason: 'ended' | 'error') => {
      if (settled) return
      settled = true
      if (isSaarthiClip(clip)) window.electronAPI?.setOrbSpeaking(false)
      console.info(`[DemoAudio] Finished ${clipName(clip)} (${reason})`)
      resolve()
    }
    audio.onended = () => finish('ended')
    audio.onerror = () => finish('error')
    void audio.play().catch(() => finish('error'))
  })
}

export async function playDemoAudioWithMinimum(clip: DemoAudioClip, minimumMs: number) {
  const startedAt = Date.now()
  await playDemoAudio(clip)
  const remaining = minimumMs - (Date.now() - startedAt)
  if (remaining > 0) await new Promise((resolve) => window.setTimeout(resolve, remaining))
}
