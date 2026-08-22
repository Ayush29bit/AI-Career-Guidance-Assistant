/**
 * The one screen.
 *
 * Layout is deliberately lopsided: the conversation takes all remaining width
 * and the insight rail is fixed at 22rem. That ratio is the product argument
 * made visually -- the panel is evidence for what is being said, not a
 * dashboard the chat happens to sit next to.
 *
 * Below `lg` the rail moves under the conversation and collapses behind a
 * toggle, so on a phone the chat is the whole screen until asked otherwise.
 */

import { useState } from 'react'
import { Chat } from '../components/Chat/Chat'
import { CareerRecommendations } from '../components/CareerRecommendations/CareerRecommendations'
import { CounsellorMark } from '../components/Chat/CounsellorMark'
import { ProfilePanel } from '../components/ProfilePanel/ProfilePanel'
import { SkillGaps } from '../components/SkillGaps/SkillGaps'
import { useConversation } from '../hooks/useConversation'

function Header({ signalCount }: { signalCount: number | null }) {
  return (
    <header className="flex shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
      <div className="flex items-center gap-2.5">
        <CounsellorMark />
        <div>
          <h1 className="text-[15px] leading-tight font-semibold text-slate-900">
            Career Counsellor
          </h1>
          <p className="text-xs text-slate-500">Conversation-led career guidance</p>
        </div>
      </div>
      {signalCount !== null && signalCount > 0 && (
        <p className="hidden text-xs text-slate-400 sm:block">
          {signalCount} {signalCount === 1 ? 'thing' : 'things'} learned about you
        </p>
      )}
    </header>
  )
}

export function CounsellorPage() {
  const conversation = useConversation()
  const [panelOpen, setPanelOpen] = useState(false)

  // The gaps shown are the top match's. The engine already ranked the careers;
  // picking anything else here would mean choosing a career on the student's
  // behalf, which the conversation is supposed to do.
  const topMatch =
    conversation.recommendations?.status === 'ok'
      ? (conversation.recommendations.matches[0] ?? null)
      : null

  const insights = (
    <div className="flex flex-col gap-3">
      <ProfilePanel profile={conversation.profile} />
      <CareerRecommendations
        recommendations={conversation.recommendations}
        refreshing={conversation.sending}
      />
      <SkillGaps match={topMatch} />
    </div>
  )

  return (
    <div className="flex h-full flex-col">
      <Header signalCount={conversation.profile?.signal_count ?? null} />

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <Chat
          messages={conversation.messages}
          sending={conversation.sending}
          starting={conversation.starting}
          ready={conversation.ready}
          error={conversation.error}
          retryable={conversation.retryable}
          onSend={conversation.send}
          onRetry={conversation.retry}
          onDismissError={conversation.dismissError}
        />

        {/* Desktop: a fixed rail beside the conversation. */}
        <aside
          aria-label="What I know so far"
          className="scroll-quiet hidden w-[22rem] shrink-0 overflow-y-auto border-l border-slate-200 bg-slate-50 p-4 lg:block"
        >
          {insights}
        </aside>

        {/* Small screens: collapsed by default so the chat owns the viewport. */}
        <div className="shrink-0 border-t border-slate-200 bg-slate-50 lg:hidden">
          <button
            type="button"
            onClick={() => setPanelOpen((open) => !open)}
            aria-expanded={panelOpen}
            className="flex w-full items-center justify-between px-4 py-3 text-left"
          >
            <span className="text-sm font-medium text-slate-700">What I know so far</span>
            <svg
              viewBox="0 0 24 24"
              className={`size-4 text-slate-400 transition-transform ${panelOpen ? 'rotate-180' : ''}`}
              fill="none"
              strokeWidth="2"
            >
              <path
                d="M6 9l6 6 6-6"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          {panelOpen && <div className="max-h-[60vh] overflow-y-auto px-4 pb-4">{insights}</div>}
        </div>
      </div>
    </div>
  )
}
