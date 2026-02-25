
document.addEventListener('DOMContentLoaded', () => {
    console.log('PersonalBrain: Custom JS initializing...');
    
    async function createFolder(name) {
        try {
            const response = await fetch('/folders', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ name: name })
            });
            
            if (response.ok) {
                const data = await response.json();
                alert(`Folder "${data.name}" created successfully!`);
                window.location.reload(); // Force reload to see changes if any
            } else {
                alert('Failed to create folder');
            }
        } catch (error) {
            console.error('PersonalBrain Error:', error);
            alert('Error creating folder');
        }
    }

    function injectButton() {
        if (document.getElementById('new-folder-btn')) return;

        // --- Strategy 1: Find "New Chat" button (Most Preferred) ---
        const buttons = Array.from(document.querySelectorAll('button, a[role="button"], a[href="/"]'));
        let newChatBtn = buttons.find(b => {
            const label = (b.getAttribute('aria-label') || '').toLowerCase();
            const title = (b.getAttribute('title') || '').toLowerCase();
            const text = (b.innerText || '').toLowerCase();
            const href = (b.getAttribute('href') || '');
            
            // Check for explicit labels
            if (label.includes('new chat') || label.includes('新会话') || 
                title.includes('new chat') || title.includes('新会话') ||
                text.includes('new chat') || text.includes('新会话')) {
                return true;
            }
            
            // Check for specific href (often used for New Chat)
            if (href === '/' || href === '') {
                // Verify it's in the sidebar header area to avoid false positives
                const parent = b.closest('header') || b.closest('.sidebar-header') || b.closest('div[style*="flex"]');
                if (parent) return true;
            }
            
            // Check for edit icon (common for new chat)
            if (b.querySelector('svg') && (label.includes('edit') || label.includes('write'))) {
                 return true;
            }
            
            return false;
        });

        // --- Strategy 2: Floating Action Button (Fallback) ---
        // If we can't find the perfect spot, add a floating button to ensure functionality
        if (!newChatBtn) {
            console.log('PersonalBrain: Could not find New Chat button, using fallback floating button.');
            
            const floatBtn = document.createElement('button');
            floatBtn.id = 'new-folder-btn';
            floatBtn.innerText = '➕ 文件夹';
            floatBtn.style.position = 'fixed';
            floatBtn.style.bottom = '20px';
            floatBtn.style.right = '20px';
            floatBtn.style.zIndex = '9999';
            floatBtn.style.padding = '10px 15px';
            floatBtn.style.borderRadius = '20px';
            floatBtn.style.backgroundColor = '#1976d2'; // Primary blue
            floatBtn.style.color = 'white';
            floatBtn.style.border = 'none';
            floatBtn.style.boxShadow = '0 2px 5px rgba(0,0,0,0.2)';
            floatBtn.style.cursor = 'pointer';
            floatBtn.style.fontWeight = 'bold';

            floatBtn.onclick = (e) => {
                const name = prompt("Enter folder name / 输入文件夹名称:");
                if (name) createFolder(name);
            };

            document.body.appendChild(floatBtn);
            return;
        }

        // --- Injection Logic ---
        if (newChatBtn) {
            console.log('PersonalBrain: Found New Chat button', newChatBtn);
            
            const folderBtn = document.createElement('button');
            folderBtn.id = 'new-folder-btn';
            folderBtn.innerHTML = '<span>📁</span>';
            folderBtn.title = 'New Folder / 新建文件夹';
            
            // Try to match the class of the sibling button for consistent styling
            folderBtn.className = newChatBtn.className; 
            
            // Force some styles to ensure visibility
            folderBtn.style.marginRight = '4px';
            folderBtn.style.display = 'inline-flex';
            folderBtn.style.alignItems = 'center';
            folderBtn.style.justifyContent = 'center';
            folderBtn.style.cursor = 'pointer';
            folderBtn.style.fontSize = '1.2rem';
            folderBtn.style.border = 'none';
            folderBtn.style.background = 'transparent';
            folderBtn.style.color = 'inherit';
            folderBtn.style.minWidth = '40px'; // Ensure clickable area
            folderBtn.style.height = '40px';

            folderBtn.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                const name = prompt("Enter folder name / 输入文件夹名称:");
                if (name) createFolder(name);
            };

            // Insert BEFORE the New Chat button
            if (newChatBtn.parentNode) {
                newChatBtn.parentNode.insertBefore(folderBtn, newChatBtn);
                console.log('PersonalBrain: Button injected into sidebar header!');
            }
        }
    }

    async function loadFolders() {
        // If container exists, check if we need to refresh (optional)
        // For now, let's keep it simple: if it exists, don't re-render unless forced
        if (document.getElementById('folder-list-container')) return;

        try {
            const response = await fetch('/folders');
            if (response.ok) {
                const folders = await response.json();
                renderFolders(folders);
            }
        } catch (error) {
            console.error('Error loading folders:', error);
        }
    }

    function renderFolders(folders) {
        const sidebar = document.querySelector('aside') || document.querySelector('[role="complementary"]');
        if (!sidebar) return;

        // More robust selector for history list or nav area
        // Often Material UI lists have class MuiList-root
        // Or we can look for the "New Chat" button's container and insert after it
        const header = sidebar.querySelector('header') || sidebar.querySelector('.sidebar-header');
        
        const container = document.createElement('div');
        container.id = 'folder-list-container';
        container.style.padding = '10px 15px';
        container.style.borderBottom = '1px solid rgba(255,255,255,0.1)';
        container.style.marginBottom = '10px';
        
        const title = document.createElement('div');
        title.innerText = 'FOLDERS';
        title.style.fontSize = '0.7rem';
        title.style.fontWeight = 'bold';
        title.style.textTransform = 'uppercase';
        title.style.letterSpacing = '1px';
        title.style.color = '#888';
        title.style.marginBottom = '8px';
        container.appendChild(title);

        if (folders.length === 0) {
            const empty = document.createElement('div');
            empty.innerText = 'No folders';
            empty.style.fontSize = '0.8rem';
            empty.style.color = '#666';
            empty.style.fontStyle = 'italic';
            container.appendChild(empty);
        } else {
            const list = document.createElement('ul');
            list.style.listStyle = 'none';
            list.style.padding = '0';
            list.style.margin = '0';
            
            folders.forEach(folder => {
                const li = document.createElement('li');
                li.style.marginBottom = '2px';
                li.style.display = 'flex';
                li.style.alignItems = 'center';
                li.style.justifyContent = 'space-between';
                li.style.cursor = 'pointer';
                li.style.padding = '6px 8px';
                li.style.borderRadius = '6px';
                li.style.transition = 'background-color 0.2s';
                
                // Hover effect
                li.onmouseenter = () => {
                    li.style.backgroundColor = 'rgba(255,255,255,0.05)';
                    const del = li.querySelector('.del-btn');
                    if (del) del.style.opacity = '1';
                };
                li.onmouseleave = () => {
                    li.style.backgroundColor = 'transparent';
                    const del = li.querySelector('.del-btn');
                    if (del) del.style.opacity = '0';
                };

                const nameGroup = document.createElement('div');
                nameGroup.style.display = 'flex';
                nameGroup.style.alignItems = 'center';
                nameGroup.style.overflow = 'hidden';

                const icon = document.createElement('span');
                icon.innerText = '📁';
                icon.style.marginRight = '8px';
                icon.style.fontSize = '1rem';
                
                const nameSpan = document.createElement('span');
                nameSpan.innerText = folder.name;
                nameSpan.style.fontSize = '0.9rem';
                nameSpan.style.whiteSpace = 'nowrap';
                nameSpan.style.overflow = 'hidden';
                nameSpan.style.textOverflow = 'ellipsis';
                
                nameGroup.appendChild(icon);
                nameGroup.appendChild(nameSpan);

                const delBtn = document.createElement('span');
                delBtn.className = 'del-btn';
                delBtn.innerHTML = '&times;';
                delBtn.title = 'Delete Folder';
                delBtn.style.color = '#ff6b6b';
                delBtn.style.opacity = '0';
                delBtn.style.transition = 'opacity 0.2s';
                delBtn.style.fontSize = '1.2rem';
                delBtn.style.padding = '0 4px';
                
                delBtn.onclick = async (e) => {
                    e.stopPropagation();
                    if (confirm(`Delete folder "${folder.name}"?`)) {
                        try {
                            const res = await fetch(`/folders/${folder.id}`, { method: 'DELETE' });
                            if (res.ok) {
                                const el = document.getElementById('folder-list-container');
                                if (el) el.remove();
                                loadFolders();
                            }
                        } catch (err) {
                            console.error(err);
                            alert('Failed to delete folder');
                        }
                    }
                };

                li.appendChild(nameGroup);
                li.appendChild(delBtn);
                
                li.onclick = () => {
                     // Placeholder for filtering
                     console.log('Selected folder:', folder.id);
                };

                list.appendChild(li);
            });
            container.appendChild(list);
        }

        // Insert logic
        // 1. Try to find the history list (nav)
        const nav = sidebar.querySelector('nav');
        if (nav) {
            nav.parentNode.insertBefore(container, nav);
            return;
        }
        
        // 2. Try to insert after header
        if (header && header.nextSibling) {
            header.parentNode.insertBefore(container, header.nextSibling);
            return;
        }

        // 3. Fallback: prepend to sidebar content
        sidebar.insertBefore(container, sidebar.firstChild);
    }


    // Run periodically to handle React re-renders
    setInterval(() => {
        injectButton();
        loadFolders();
    }, 2000);
    injectButton();
    loadFolders();
});
