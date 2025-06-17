// Test script to verify the connection between frontend and backend
import fetch from 'node-fetch';

// Sample base64 image (a small 1x1 pixel transparent PNG)
// In a real scenario, you would use a real medical document image
const sampleBase64Image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

// Backend endpoint URL
const backendUrl = 'https://patient-referral-match-934163632848.us-central1.run.app';

async function testBackendConnection() {
  console.log('Testing connection to backend endpoint:', backendUrl);
  
  try {
    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ image: sampleBase64Image })
    });
    
    if (!response.ok) {
      throw new Error(`HTTP error! Status: ${response.status}`);
    }
    
    const data = await response.json();
    console.log('Backend Response:', data);
    
    // Check if the response contains the expected attributes
    if (data.name !== undefined && 
        data.date_of_birth !== undefined && 
        data.date_of_first_procedure !== undefined) {
      console.log('✅ Backend integration is working correctly!');
      console.log('Extracted attributes:');
      console.log('- Name:', data.name || 'N/A');
      console.log('- Date of Birth:', data.date_of_birth || 'N/A');
      console.log('- Date of First Procedure:', data.date_of_first_procedure || 'N/A');
    } else {
      console.log('❌ Backend response does not contain the expected attributes.');
      console.log('Actual response:', data);
    }
  } catch (error) {
    console.error('Error connecting to backend:', error.message);
  }
}

// Run the test
testBackendConnection();
