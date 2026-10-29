/**
 * Why this exists: to hear how she is, in her own words — what she said
 * to Saathi and what it said back, newest first, with the recording when
 * the device kept one. One player for the whole list: two clips talking
 * over each other is never what anyone wants.
 */
import { useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshControl, StyleSheet, View } from 'react-native';

import { clipUrl, listTurns, type Turn, type TurnCursor } from '../../data';
import { useDevice } from '../../state/session';
import { Body, Button, Card, Empty, Loading, Muted, Notice, Screen } from '../../ui/kit';
import { color, font, space } from '../../ui/theme';

function when(d: Date | null): string {
  if (!d) return '';
  const sameDay = d.toDateString() === new Date().toDateString();
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  return sameDay ? `Today, ${time}` : `${d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}, ${time}`;
}

export default function Conversations() {
  const device = useDevice();
  const [turns, setTurns] = useState<Turn[] | null>(null);
  const [next, setNext] = useState<TurnCursor | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const player = useAudioPlayer();
  const status = useAudioPlayerStatus(player);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [loadingClip, setLoadingClip] = useState<string | null>(null);
  const generation = useRef(0);

  const loadFirst = useCallback(async () => {
    const gen = ++generation.current;
    setError(null);
    try {
      const page = await listTurns(device.id);
      if (gen !== generation.current) return;
      setTurns(page.turns);
      setNext(page.next);
    } catch (e) {
      if (gen === generation.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [device.id]);

  useEffect(() => {
    setTurns(null);
    loadFirst();
  }, [loadFirst]);

  useEffect(() => {
    if (status.didJustFinish) setPlayingId(null);
  }, [status.didJustFinish]);

  const more = async () => {
    if (!next) return;
    const gen = generation.current;
    setLoadingMore(true);
    try {
      const page = await listTurns(device.id, { after: next });
      if (gen !== generation.current) return;
      setTurns((t) => [...(t ?? []), ...page.turns]);
      setNext(page.next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  const toggle = async (turn: Turn) => {
    if (!turn.clipPath) return;
    if (playingId === turn.id && status.playing) {
      player.pause();
      setPlayingId(null);
      return;
    }
    setLoadingClip(turn.id);
    try {
      const uri = await clipUrl(turn.clipPath);
      player.replace({ uri });
      player.play();
      setPlayingId(turn.id);
    } catch (e) {
      setError(`Couldn’t play that recording: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoadingClip(null);
    }
  };

  if (!turns && !error) return <Loading label="Loading conversations" />;

  return (
    <Screen
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={async () => {
            setRefreshing(true);
            await loadFirst();
            setRefreshing(false);
          }}
        />
      }
    >
      {error ? <Notice>{error}</Notice> : null}
      {turns && turns.length === 0 ? <Empty>No conversations yet. They appear here after she talks to Saathi.</Empty> : null}
      {turns?.map((t) => {
        const playing = playingId === t.id && status.playing;
        return (
          <Card key={t.id}>
            <Muted>{when(t.ts)}</Muted>
            {t.heard ? (
              <View accessible accessibilityLabel={`She said: ${t.heard}`}>
                <Body style={styles.who}>She said</Body>
                <Body style={styles.heard}>{t.heard}</Body>
              </View>
            ) : null}
            {t.said ? (
              <View accessible accessibilityLabel={`Saathi said: ${t.said}`}>
                <Body style={styles.who}>Saathi said</Body>
                <Body>{t.said}</Body>
              </View>
            ) : null}
            {t.clipPath ? (
              <Button
                variant="secondary"
                label={playing ? 'Pause' : 'Play recording'}
                accessibilityLabel={playing ? 'Pause recording' : `Play recording from ${when(t.ts)}`}
                busy={loadingClip === t.id}
                onPress={() => toggle(t)}
              />
            ) : null}
          </Card>
        );
      })}
      {next ? <Button variant="secondary" label="Show older" onPress={more} busy={loadingMore} /> : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  who: { fontSize: font.small, fontWeight: '700', color: color.muted, marginTop: space.xs },
  heard: { fontWeight: '600' },
});
