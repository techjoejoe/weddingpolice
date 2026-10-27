// Shared Firebase setup for the contest form and the contest admin page.
// These config values are public by design; access is controlled by Firestore security rules.
import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js';
import { getFirestore } from 'https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js';
import { getAuth } from 'https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js';

const firebaseConfig = {
  apiKey: 'AIzaSyCD0jDQUSwBp-8T5CoUslYGxOlN1HecHAg',
  authDomain: 'theweddingpolice.firebaseapp.com',
  projectId: 'theweddingpolice',
  storageBucket: 'theweddingpolice.firebasestorage.app',
  messagingSenderId: '1089837881895',
  appId: '1:1089837881895:web:9c221701551e41e8424c02'
};

export const app = initializeApp(firebaseConfig);
export const db = getFirestore(app);
export const auth = getAuth(app);

export const ENTRIES_COLLECTION = 'contestEntries';
