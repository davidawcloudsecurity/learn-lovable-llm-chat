type Message = { role: "user" | "assistant"; content: string };

export type ResponseMeta = { elapsed: number; credits: number; inputTokens: number; outputTokens: number };

export async function streamChatResponse(
  messages: Message[],
  onDelta: (text: string) => void,
  onDone: () => void,
  onError: (error: string) => void,
  onMeta?: (meta: ResponseMeta) => void
) {
  try {
    const response = await fetch('/api/strands', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ messages }),
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();

    if (!reader) {
      throw new Error('No response body');
    }

    while (true) {
      const { done, value } = await reader.read();

      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);

          if (data === '[DONE]') {
            onDone();
            return;
          }

          try {
            const parsed = JSON.parse(data);
            if (typeof parsed.text === "string") {
              onDelta(parsed.text);
            }
            if (parsed.elapsed != null && parsed.credits != null) {
              onMeta?.({
                elapsed: parsed.elapsed,
                credits: parsed.credits,
                inputTokens: parsed.input_tokens ?? 0,
                outputTokens: parsed.output_tokens ?? 0,
              });
            } else if (parsed.error) {
              onError(parsed.error);
              return;
            }
          } catch (e) {
            // Skip invalid JSON
          }
        }
      }
    }

    onDone();
  } catch (error) {
    console.error('Stream error:', error);
    onError(error instanceof Error ? error.message : 'Unknown error');
  }
}
