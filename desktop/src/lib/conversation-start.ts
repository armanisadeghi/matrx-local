export interface ConversationStartFields {
  conversation_id: string;
  is_new: true;
  store: boolean;
}

export interface ConversationStartRequest {
  url: string;
  body: Record<string, unknown> & ConversationStartFields;
  startedConversationId: string;
}

/**
 * Build the required conversation-start wire contract shared by AIDream and
 * the local /ai mirror. The client always mints the correlation ID; `store`
 * is the only persistence switch and `is_new` only asserts creation.
 */
export function buildConversationStartRequest(
  url: string,
  body: Record<string, unknown>,
  options: {
    store?: boolean;
    conversationId?: string;
  } = {},
): ConversationStartRequest {
  const conversationId = options.conversationId ?? crypto.randomUUID();
  return {
    url,
    body: {
      ...body,
      conversation_id: conversationId,
      is_new: true,
      store: options.store ?? true,
    },
    startedConversationId: conversationId,
  };
}
