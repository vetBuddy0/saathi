/**
 * Why this exists: a few words to her, said aloud by Saathi, and her
 * answer back. Each message says plainly whether she has heard it yet —
 * "Waiting for Saathi to say it" or "Saathi said it at 4:12" — because a
 * caregiver's next move (call her? wait?) depends on exactly that.
 *
 * Messages from every caregiver of this device show, each with who sent
 * it: her daughter should see that her brother already asked about lunch.
 */
import { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import {
  MAX_MESSAGE_LENGTH,
  type Message,
  type Reply,
  sendMessage,
  validateMessage,
  watchMessages,
  watchReplies,
} from '../../data';
import { useDevice, useSession } from '../../state/session';
import { Body, Button, Card, Empty, Field, Heading, Loading, Muted, Notice, Screen } from '../../ui/kit';
import { color, font, radius, space } from '../../ui/theme';

const time = (d: Date | null) => (d ? d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '');
const day = (d: Date | null) =>
  d ? (d.toDateString() === new Date().toDateString() ? 'Today' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })) : '';

/**
 * One listener per *spoken* message: she can only reply to what she has
 * heard, so a message still waiting has nothing to listen for. That
 * keeps the open listeners to the handful she's actually answering.
 * Lost: one collection-group query over every reply. The rules match
 * replies only under their device's path, and a collection-group read
 * would need its own rule and index.
 */
function Replies({ deviceId, msgId }: { deviceId: string; msgId: string }) {
  const [replies, setReplies] = useState<Reply[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => watchReplies(deviceId, msgId, setReplies, (e) => setError(e.message)), [deviceId, msgId]);
  if (error) return <Muted>Couldn’t load her replies: {error}</Muted>;
  if (replies.length === 0) return null;
  return (
    <View style={styles.replies}>
      {replies.map((r) => (
        <View key={r.id} style={styles.reply} accessible accessibilityLabel={`She replied at ${time(r.ts)}: ${r.text}`}>
          <Muted style={styles.replyWho}>She replied · {time(r.ts)}</Muted>
          <Body>{r.text}</Body>
        </View>
      ))}
    </View>
  );
}

export default function Messages() {
  const device = useDevice();
  const { user } = useSession();
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    setMessages(null);
    setLoadError(null);
    return watchMessages(device.id, setMessages, (e) => setLoadError(e.message), 20);
  }, [device.id]);

  const send = async () => {
    const problem = validateMessage(draft);
    if (problem) {
      setError(problem);
      return;
    }
    setSending(true);
    setError(null);
    try {
      await sendMessage(device.id, draft);
      setDraft('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <Screen>
      <Card>
        <Heading>Send her a message</Heading>
        <Field
          label="Message"
          hint={`Saathi reads it aloud: “A message from ${user?.displayName || 'you'}…”`}
          placeholder="For example: Coming over on Sunday, Ma"
          value={draft}
          onChangeText={(t) => {
            setDraft(t);
            if (error) setError(null);
          }}
          multiline
          maxLength={MAX_MESSAGE_LENGTH}
          style={styles.compose}
          error={error}
        />
        <Button label="Send" onPress={send} busy={sending} disabled={!draft.trim()} />
      </Card>

      {loadError ? <Notice>Couldn’t load messages: {loadError}</Notice> : null}
      {!messages && !loadError ? <Loading label="Loading messages" /> : null}
      {messages && messages.length === 0 ? <Empty>No messages yet. What you send appears here.</Empty> : null}
      {messages?.map((m) => {
        const spoken = m.spokenAt
          ? `Saathi said it at ${time(m.spokenAt)}${day(m.spokenAt) !== 'Today' ? `, ${day(m.spokenAt)}` : ''}`
          : 'Waiting for Saathi to say it';
        const mine = m.fromUid === user?.uid;
        return (
          <Card key={m.id}>
            <Muted>
              {mine ? 'You' : m.from} · {day(m.createdAt)} {time(m.createdAt)}
            </Muted>
            <Body>{m.text}</Body>
            <View
              style={[styles.status, m.spokenAt ? styles.spoken : styles.waiting]}
              accessible
              accessibilityLabel={spoken}
            >
              <Body style={[styles.statusText, { color: m.spokenAt ? color.taken : color.pending }]}>
                {m.spokenAt ? '✓ ' : '… '}
                {spoken}
              </Body>
            </View>
            {m.spokenAt ? <Replies deviceId={device.id} msgId={m.id} /> : null}
          </Card>
        );
      })}
    </Screen>
  );
}

const styles = StyleSheet.create({
  compose: { minHeight: 110, textAlignVertical: 'top' },
  status: { alignSelf: 'flex-start', borderRadius: radius, paddingHorizontal: space.sm, paddingVertical: 2 },
  spoken: { backgroundColor: color.takenBg },
  waiting: { backgroundColor: color.pendingBg },
  statusText: { fontSize: font.small, fontWeight: '700' },
  replies: { gap: space.sm, marginTop: space.xs },
  reply: {
    backgroundColor: color.bg,
    borderLeftWidth: 4,
    borderLeftColor: color.primary,
    borderRadius: 6,
    padding: space.sm,
  },
  replyWho: { fontWeight: '700' },
});
