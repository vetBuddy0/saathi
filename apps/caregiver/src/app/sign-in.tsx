/**
 * Why this exists: the way in. Google comes first, as one big button,
 * because the owner prefers it and most of the family already has a
 * Google account on their phone. Email is a quiet second choice that
 * opens a form, for anyone who'd rather not use Google. Both signing in
 * and making an account happen on this one screen, so a relative handed
 * the app by a sibling doesn't have to work out which they need. The
 * name asked for on an email sign-up is the name Saathi says aloud
 * before their message, and the field explains that.
 */
import { router } from 'expo-router';
import { useState } from 'react';

import { authErrorMessage, signIn, signUp } from '../data';
import { GoogleSignIn } from '../ui/GoogleSignIn';
import { Body, Button, Card, Field, Muted, Notice, Screen, Title } from '../ui/kit';

export default function SignIn() {
  const [useEmail, setUseEmail] = useState(false);
  const [mode, setMode] = useState<'in' | 'up'>('in');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const done = () => router.replace('/');
  const fail = (e: unknown) => setError(authErrorMessage(e));

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (mode === 'up') await signUp(email, password, name);
      else await signIn(email, password);
      done();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Title>Welcome</Title>
      <Body>See how she is, send her a message, and help with her reminders.</Body>
      <Card>
        <GoogleSignIn
          onSignedIn={done}
          onError={(e) => {
            setError(null);
            fail(e);
          }}
        />
        {!useEmail && error ? <Notice>{error}</Notice> : null}
      </Card>

      {!useEmail ? (
        <Button
          variant="link"
          label="Use email and password instead"
          onPress={() => {
            setUseEmail(true);
            setError(null);
          }}
        />
      ) : (
        <Card>
          <Muted>{mode === 'in' ? 'Sign in with email' : 'Create an account with email'}</Muted>
          {mode === 'up' ? (
            <Field
              label="Your name"
              hint="Saathi says this before your messages, like “A message from Ravi”."
              value={name}
              onChangeText={setName}
              autoComplete="name"
              textContentType="name"
              returnKeyType="next"
            />
          ) : null}
          <Field
            label="Email"
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            autoComplete="email"
            keyboardType="email-address"
            textContentType="emailAddress"
            returnKeyType="next"
          />
          <Field
            label="Password"
            hint={mode === 'up' ? 'At least 6 characters.' : undefined}
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            autoComplete={mode === 'up' ? 'new-password' : 'current-password'}
            textContentType={mode === 'up' ? 'newPassword' : 'password'}
            returnKeyType="go"
            onSubmitEditing={submit}
          />
          {error ? <Notice>{error}</Notice> : null}
          <Button
            variant="secondary"
            label={mode === 'in' ? 'Sign in' : 'Create account'}
            onPress={submit}
            busy={busy}
          />
          <Button
            variant="link"
            label={mode === 'in' ? 'New here? Create an account' : 'Already have an account? Sign in'}
            onPress={() => {
              setMode(mode === 'in' ? 'up' : 'in');
              setError(null);
            }}
          />
        </Card>
      )}
    </Screen>
  );
}
