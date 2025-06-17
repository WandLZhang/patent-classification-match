// Load environment variables from meta tags
const getEnvVar = (name) => {
    const meta = document.querySelector(`meta[name="${name}"]`);
    return meta ? meta.content : '';
};

export const config = {
    firebase: {
        apiKey: getEnvVar('firebase-api-key'),
        authDomain: "gemini-med-lit-review.firebaseapp.com",
        projectId: "gemini-med-lit-review",
        storageBucket: "gemini-med-lit-review.firebasestorage.app",
        messagingSenderId: "934163632848",
        appId: "1:934163632848:web:621139404479e7562e44d5",
        measurementId: "G-Y4Y3EGC8KZ"
    },
    googleMaps: {
        apiKey: getEnvVar('google-maps-api-key')
    }
};
