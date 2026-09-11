import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';

// No <SigmaClientProvider> here on purpose: `client` from the SDK is already a
// pre-initialized instance, and the official sample plugins use the hooks with
// no provider. Wrap in one only if you call `initialize()` to make your own.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
