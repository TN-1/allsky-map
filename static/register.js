/**
 * Indi-Allsky Map Client-side Registration Helper
 */

document.addEventListener('DOMContentLoaded', () => {
    const genKeyBtn = document.getElementById('gen-key-btn');
    if (genKeyBtn) {
        genKeyBtn.addEventListener('click', registerCamera);
    }
    
    const copyBtn = document.getElementById('copy-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', copyKey);
    }
});

async function registerCamera(event) {
    if (event) event.stopPropagation();
    
    const btn = document.getElementById('gen-key-btn');
    const keyArea = document.getElementById('key-display-area');
    const keyValue = document.getElementById('api-key-value');

    if (!btn || !keyArea || !keyValue) return;

    btn.disabled = true;
    btn.textContent = "Generating...";

    try {
        const response = await fetch('/api/register', { method: 'POST' });
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        
        const data = await response.json();
        if (data.api_key) {
            keyValue.textContent = data.api_key;
            keyArea.classList.remove('hidden');
            btn.classList.add('hidden');
        }
    } catch (err) {
        console.error("API Error:", err);
        alert("Could not connect to the server to generate a key.");
        btn.disabled = false;
        btn.textContent = "Generate API Key";
    }
}

function copyKey() {
    const keyValue = document.getElementById('api-key-value');
    const copyBtn = document.getElementById('copy-btn');

    if (!keyValue || !copyBtn) return;

    const keyText = keyValue.textContent;

    navigator.clipboard.writeText(keyText)
        .then(() => {
            const originalText = copyBtn.textContent;
            copyBtn.textContent = "Copied!";
            copyBtn.classList.add('success');
            
            setTimeout(() => {
                copyBtn.textContent = originalText;
                copyBtn.classList.remove('success');
            }, 2000);
        })
        .catch(err => {
            console.error("Failed to copy API key to clipboard: ", err);
            // Fallback selection if clipboard permission is denied
            const range = document.createRange();
            range.selectNode(keyValue);
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(range);
            alert("Could not write to clipboard automatically. The key is now selected—please press Ctrl+C to copy manually.");
        });
}
