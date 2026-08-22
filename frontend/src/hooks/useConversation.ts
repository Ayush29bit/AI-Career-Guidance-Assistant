/**
 * All of the app's state, in one hook.
 *
 * There is no store and no reducer because there is no shared state to
 * coordinate: one conversation, owned by one screen, updated by one request at
 * a time. A state library here would be scaffolding around four `useState`
 * calls.
 *
 * The important design point is that the profile and the recommendations are
 * never fetched separately. Every turn returns all three together, so what the
 * panel shows is always exactly what the counsellor was looking at when it
 * replied. Fetching them independently would let the prose and the panel drift
 * apart between renders.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { createConversation, sendMessage } from '../api/conversation'
import type {
  ProfileResponse,
  RecommendationsResponse,
  TurnResponse,
  TurnStatus,
} from '../types/api'

/** One turn as the UI holds it. */
export interface ChatMessage {
  /** Server id where there is one; a local key for messages awaiting a reply. */
  key: string
  role: 'user' | 'assistant'
  content: string
  createdAt: string
  /**
   * Set on assistant turns the server did not persist -- the LLM was
   * unreachable and this text stood in for a reply. Shown as a notice rather
   * than as something the counsellor said.
   */
  fallbackStatus?: Exclude<TurnStatus, 'ok'>
}

export interface ConversationState {
  ready: boolean
  starting: boolean
  sending: boolean
  messages: ChatMessage[]
  profile: ProfileResponse | null
  recommendations: RecommendationsResponse | null
  /** A request that failed outright. The turn did not happen. */
  error: string | null
  /** The text of the message that failed, so it can be sent again. */
  retryable: string | null
  send: (content: string) => Promise<void>
  retry: () => Promise<void>
  dismissError: () => void
}

let localKey = 0
const nextKey = () => `local-${++localKey}`

export function useConversation(): ConversationState {
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [starting, setStarting] = useState(true)
  const [sending, setSending] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [profile, setProfile] = useState<ProfileResponse | null>(null)
  const [recommendations, setRecommendations] = useState<RecommendationsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retryable, setRetryable] = useState<string | null>(null)

  // React 18+ StrictMode mounts effects twice in development. Without this the
  // app would create two conversations on every page load and quietly talk to
  // the second one.
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true

    createConversation()
      .then((conversation) => setConversationId(conversation.conversation_id))
      .catch((failure: unknown) => {
        setError(
          failure instanceof ApiError
            ? failure.message
            : 'Could not start a conversation. Please reload the page.',
        )
      })
      .finally(() => setStarting(false))
  }, [])

  const applyTurn = useCallback((turn: TurnResponse, sent: string) => {
    setProfile(turn.profile)
    // Only replace the ranking when this turn produced one. A turn that failed
    // early returns null, and blanking the panel would look like the engine had
    // changed its mind rather than that nothing new was computed.
    if (turn.recommendations) {
      setRecommendations(turn.recommendations)
    }

    setMessages((current) => [
      ...current,
      {
        key: turn.message.id ? `assistant-${turn.message.id}` : nextKey(),
        role: 'assistant',
        content: turn.message.content,
        createdAt: turn.message.created_at ?? new Date().toISOString(),
        fallbackStatus: turn.status === 'ok' ? undefined : turn.status,
      },
    ])

    // A turn can succeed as a request and still fail as a conversation: the
    // message was stored and the profile updated, but the LLM never produced a
    // reply. Offering the text back for another attempt is the only recovery,
    // since there is no endpoint that re-answers an existing turn.
    if (turn.status !== 'ok') {
      setRetryable(sent)
    }
  }, [])

  const deliver = useCallback(
    async (content: string) => {
      if (!conversationId) return

      setError(null)
      setRetryable(null)
      setSending(true)
      // The student's own words appear immediately. They are already on screen
      // by the time the request is in flight, which is what makes the wait feel
      // like the counsellor thinking rather than the app hanging.
      const pending: ChatMessage = {
        key: nextKey(),
        role: 'user',
        content,
        createdAt: new Date().toISOString(),
      }
      setMessages((current) => [...current, pending])

      try {
        const turn = await sendMessage(conversationId, content)
        // Swap the local echo for the stored row, so the key and timestamp are
        // the server's.
        setMessages((current) =>
          current.map((message) =>
            message.key === pending.key
              ? {
                  ...message,
                  key: `user-${turn.user_message.id}`,
                  createdAt: turn.user_message.created_at,
                }
              : message,
          ),
        )
        applyTurn(turn, content)
      } catch (failure: unknown) {
        // The turn did not happen. Take the echoed message back rather than
        // leaving it looking sent, and offer it for retry.
        setMessages((current) => current.filter((message) => message.key !== pending.key))
        setError(
          failure instanceof ApiError
            ? failure.message
            : 'Something went wrong. Please try again.',
        )
        setRetryable(content)
      } finally {
        setSending(false)
      }
    },
    [applyTurn, conversationId],
  )

  const send = useCallback(
    async (content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return
      await deliver(trimmed)
    },
    [deliver],
  )

  const retry = useCallback(async () => {
    if (!retryable) return
    await deliver(retryable)
  }, [deliver, retryable])

  const dismissError = useCallback(() => {
    setError(null)
    setRetryable(null)
  }, [])

  return {
    ready: conversationId !== null,
    starting,
    sending,
    messages,
    profile,
    recommendations,
    error,
    retryable,
    send,
    retry,
    dismissError,
  }
}
