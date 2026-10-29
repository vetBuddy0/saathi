/**
 * Why this exists: the screen a worried son opens at lunch. A one-line
 * answer first ("2 taken, 1 missed, 1 still to come"), then each
 * reminder with a mark and a word — ✓ Taken, ✗ Missed, … Not yet — so it
 * reads the same in greyscale, to a screen reader, and to someone
 * colour-blind.
 */
import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { summarize, type TodayItem, watchToday } from '../../data';
import { useDevice } from '../../state/session';
import { Body, Card, Empty, Loading, Muted, Notice, Screen, Title } from '../../ui/kit';
import { color, font, radius, space } from '../../ui/theme';

const LOOK = {
  taken: { mark: '✓', word: 'Taken', fg: color.taken, bg: color.takenBg },
  missed: { mark: '✗', word: 'Missed', fg: color.missed, bg: color.missedBg },
  pending: { mark: '…', word: 'Not yet', fg: color.pending, bg: color.pendingBg },
} as const;

function detail(item: TodayItem): string | null {
  const e = item.lastEvent;
  if (!e?.ts) return null;
  const at = e.ts.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  if (e.kind === 'acked') return `She said done at ${at}`;
  if (e.kind === 'missed') return `No answer after ${at}`;
  if (e.kind === 'fired') return `Saathi reminded her at ${at}`;
  return null;
}

export default function Today() {
  const device = useDevice();
  const [items, setItems] = useState<TodayItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(null);
    setError(null);
    return watchToday(device.id, setItems, (e) => setError(e.message));
  }, [device.id]);

  if (error) {
    return (
      <Screen>
        <Notice>Couldn’t load today: {error}</Notice>
      </Screen>
    );
  }
  if (!items) return <Loading label="Loading today" />;

  const s = summarize(items);
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
  const parts = [
    s.taken ? `${s.taken} taken` : null,
    s.missed ? `${s.missed} missed` : null,
    s.pending ? `${s.pending} still to come` : null,
  ].filter(Boolean);

  return (
    <Screen>
      <Title>{today}</Title>
      {items.length === 0 ? (
        <Empty>No reminders today. You can add one in Reminders.</Empty>
      ) : (
        <Body accessibilityRole="summary">{parts.join(', ')}.</Body>
      )}
      {items.map((item) => {
        const look = LOOK[item.status];
        const more = detail(item);
        return (
          <Card
            key={item.key}
            accessible
            accessibilityLabel={`${item.time}, ${item.text}: ${look.word}.${more ? ` ${more}.` : ''}`}
            style={styles.row}
          >
            <Text style={styles.time}>{item.time}</Text>
            <View style={styles.middle}>
              <Body style={styles.text}>{item.text}</Body>
              {more ? <Muted>{more}</Muted> : null}
            </View>
            <View style={[styles.pill, { backgroundColor: look.bg, borderColor: look.fg }]}>
              <Text style={[styles.pillText, { color: look.fg }]}>
                {look.mark} {look.word}
              </Text>
            </View>
          </Card>
        );
      })}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, flexWrap: 'wrap' },
  time: { fontSize: font.heading, fontWeight: '700', color: color.text, minWidth: 72, fontVariant: ['tabular-nums'] },
  middle: { flex: 1, minWidth: 140, gap: 2 },
  text: { fontWeight: '600' },
  pill: { borderRadius: radius, borderWidth: 2, paddingHorizontal: space.md, paddingVertical: space.sm },
  pillText: { fontSize: font.small, fontWeight: '800' },
});
