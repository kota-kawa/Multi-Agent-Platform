document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('memoryForm');
  const longTermMemoryTextarea = document.getElementById('longTermMemory');
  const shortTermMemoryTextarea = document.getElementById('shortTermMemory');
  const statusMessage = document.getElementById('statusMessage');

  // Fetch initial memory data
  fetch('/api/memory')
    .then(response => response.json())
    .then(data => {
      longTermMemoryTextarea.value = data.long_term_memory;
      shortTermMemoryTextarea.value = data.short_term_memory;
    })
    .catch(error => {
      console.error('Error fetching memory:', error);
      statusMessage.textContent = 'Failed to load memory data.';
    });

  // Handle form submission
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const longTermMemory = longTermMemoryTextarea.value;
    const shortTermMemory = shortTermMemoryTextarea.value;

    fetch('/api/memory', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        long_term_memory: longTermMemory,
        short_term_memory: shortTermMemory,
      }),
    })
    .then(response => response.json())
    .then(data => {
      statusMessage.textContent = data.message;
      setTimeout(() => {
        statusMessage.textContent = '';
      }, 3000);
    })
    .catch(error => {
      console.error('Error saving memory:', error);
      statusMessage.textContent = 'Failed to save memory data.';
    });
  });
});
