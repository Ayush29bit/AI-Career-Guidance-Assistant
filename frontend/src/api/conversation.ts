/**
 * The counselling endpoints, one function each.
 *
 * Thin on purpose: these exist so components name an action ("send a message")
 * rather than a URL, and so the whole API surface the frontend uses is visible
 * in one screen of code.
 */

import { api } from './client'
import type {
  ConversationCreated,
  ConversationDetail,
  ProfileResponse,
  RecommendationsResponse,
  TurnResponse,
} from '../types/api'

/** Start an anonymous conversation. Creates the student profile alongside it. */
export function createConversation(): Promise<ConversationCreated> {
  return api.post<ConversationCreated>('/conversations')
}

/** The full message history, for reloading an existing conversation. */
export function getConversation(conversationId: string): Promise<ConversationDetail> {
  return api.get<ConversationDetail>(`/conversations/${conversationId}`)
}

/**
 * Send one message and get the whole turn back: the counsellor's reply, the
 * updated profile, and the ranking the reply was based on.
 *
 * One request, not three. The profile and recommendations in the response are
 * the ones this turn actually used, so the panel can never drift out of step
 * with what the counsellor just said.
 */
export function sendMessage(conversationId: string, content: string): Promise<TurnResponse> {
  return api.post<TurnResponse>(`/conversations/${conversationId}/messages`, { content })
}

/** The stored profile on its own. Not used by the chat flow, which gets it back
 * with every turn -- kept for reloading a conversation from its id. */
export function getProfile(profileId: string): Promise<ProfileResponse> {
  return api.get<ProfileResponse>(`/profile/${profileId}`)
}

/** The ranking on its own, for the same reload case. */
export function getRecommendations(
  profileId: string,
  limit = 5,
): Promise<RecommendationsResponse> {
  return api.get<RecommendationsResponse>(`/profile/${profileId}/recommendations?limit=${limit}`)
}
