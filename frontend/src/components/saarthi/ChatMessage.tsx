import React, { useState } from 'react'
import {
  Compass,
  Copy,
  Check,
} from 'lucide-react'
import type { ChatMessageItem } from '../../types/browser'

interface ChatMessageProps {
  message: ChatMessageItem
}

export const ChatMessage: React.FC<ChatMessageProps> = ({
  message,
}) => {
  const [copied, setCopied] = useState(false)
  const isUser = message.sender === 'user'

  const handleCopy = () => {
    navigator.clipboard?.writeText(message.text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  // Basic markdown bold highlighter helper
  const renderFormattedText = (content: string) => {
    const parts = content.split(/(\*\*.*?\*\*)/g)
    return parts.map((part, index) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return (
          <strong key={index} className="text-white font-semibold">
            {part.slice(2, -2)}
          </strong>
        )
      }
      return part
    })
  }

  if (isUser) {
    return (
      <div className="flex justify-end mb-3">
        <div className="max-w-[85%] bg-indigo-600/90 text-white rounded-2xl rounded-tr-xs px-3.5 py-2.5 shadow-sm text-xs leading-relaxed font-sans">
          <p className="whitespace-pre-wrap">{message.text}</p>
          <div className="text-[10px] text-indigo-200/70 text-right mt-1 font-mono">
            {message.timestamp}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 mb-4 group">
      {/* Assistant Avatar & Header */}
      <div className="flex items-center justify-between text-xs text-neutral-400">
        <div className="flex items-center gap-1.5">
          <div className="w-5 h-5 rounded-md bg-indigo-500/20 text-indigo-400 flex items-center justify-center border border-indigo-500/30">
            <Compass className="w-3 h-3" />
          </div>
          <span className="font-semibold text-neutral-200 text-xs">SAARTHI</span>
          <span className="text-[10px] text-neutral-400 font-mono">
            {message.timestamp}
          </span>
        </div>

        <button
          type="button"
          onClick={handleCopy}
          className="opacity-0 group-hover:opacity-100 p-1 text-neutral-400 hover:text-white rounded transition-opacity"
          title="Copy message"
        >
          {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
        </button>
      </div>

      {/* Message Body */}
      <div className="bg-neutral-900/90 border border-neutral-800 rounded-xl p-3 text-xs leading-relaxed text-neutral-300 font-sans shadow-xs">
        <div className="whitespace-pre-wrap">{renderFormattedText(message.text)}</div>
      </div>
    </div>
  )
}
