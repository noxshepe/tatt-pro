/**
 * TattPro UI System - Design Tokens & User Feedback
 * Toast notifications, Confirm dialogs with Undo, Skeletons & Empty states
 */

// 1. TOAST NOTIFICATIONS (Success, Error, Warning, Info)
function showToast(message, type = 'success', duration = 3500) {
    let container = document.getElementById('app-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'app-toast-container';
        container.className = 'fixed bottom-5 right-5 z-[999999] flex flex-col gap-2 pointer-events-none';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    const isError = type === 'error';
    const isWarning = type === 'warning';
    const isInfo = type === 'info';

    let bgClass = 'bg-slate-900 border border-slate-700 text-white';
    let iconClass = 'fa-circle-check text-emerald-400';

    if (isError) {
        bgClass = 'bg-rose-600 text-white shadow-rose-900/20';
        iconClass = 'fa-circle-exclamation text-rose-200';
    } else if (isWarning) {
        bgClass = 'bg-amber-600 text-white font-bold shadow-amber-900/20';
        iconClass = 'fa-triangle-exclamation text-amber-200';
    } else if (isInfo) {
        bgClass = 'bg-indigo-600 text-white shadow-indigo-900/20';
        iconClass = 'fa-circle-info text-indigo-200';
    }

    toast.className = `pointer-events-auto flex items-center gap-2.5 px-4 py-3 rounded-2xl shadow-xl text-xs font-semibold transition-all transform duration-300 translate-y-3 opacity-0 max-w-sm ${bgClass}`;
    toast.innerHTML = `<i class="fa-solid ${iconClass} text-sm shrink-0"></i><span class="flex-1 leading-snug">${message}</span>`;
    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.classList.remove('translate-y-3', 'opacity-0');
        toast.classList.add('translate-y-0', 'opacity-100');
    });

    const timer = setTimeout(() => {
        toast.classList.remove('translate-y-0', 'opacity-100');
        toast.classList.add('translate-y-3', 'opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, duration);

    return {
        dismiss: () => {
            clearTimeout(timer);
            toast.remove();
        }
    };
}

// 2. TOAST HOÀN TÁC (UNDO TOAST) CHO THAO TÁC XÓA
function showUndoToast(options) {
    const {
        message = 'Đã xóa mục thành công',
        actionLabel = 'Hoàn tác',
        onUndo = null,
        duration = 6000
    } = options;

    let container = document.getElementById('app-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'app-toast-container';
        container.className = 'fixed bottom-5 right-5 z-[999999] flex flex-col gap-2 pointer-events-none';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = 'pointer-events-auto flex items-center justify-between gap-3 px-4 py-3 rounded-2xl shadow-2xl bg-slate-900 border border-slate-700 text-white text-xs font-medium transition-all transform duration-300 translate-y-3 opacity-0 min-w-[280px] max-w-md';
    
    let secondsLeft = Math.ceil(duration / 1000);
    toast.innerHTML = `
        <div class="flex items-center gap-2.5 min-w-0">
            <i class="fa-solid fa-trash-can text-rose-400 text-sm shrink-0"></i>
            <span class="truncate text-slate-200">${message}</span>
        </div>
        <div class="flex items-center gap-2 shrink-0">
            <button type="button" class="btn-undo font-extrabold text-white hover:text-white bg-amber-600 hover:bg-amber-500 transition px-2.5 py-1 rounded-lg shadow-sm border border-amber-400/50 flex items-center gap-1 cursor-pointer">
                ${actionLabel} (<span class="countdown">${secondsLeft}</span>s)
            </button>
            <button type="button" class="btn-close-undo text-slate-500 hover:text-white transition text-sm px-1">&times;</button>
        </div>
    `;
    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.classList.remove('translate-y-3', 'opacity-0');
        toast.classList.add('translate-y-0', 'opacity-100');
    });

    const countdownEl = toast.querySelector('.countdown');
    const interval = setInterval(() => {
        secondsLeft -= 1;
        if (countdownEl && secondsLeft > 0) {
            countdownEl.innerText = secondsLeft;
        }
    }, 1000);

    const removeToast = () => {
        clearInterval(interval);
        toast.classList.remove('translate-y-0', 'opacity-100');
        toast.classList.add('translate-y-3', 'opacity-0');
        setTimeout(() => toast.remove(), 300);
    };

    const timer = setTimeout(removeToast, duration);

    // Xử lý nút Hoàn tác
    const undoBtn = toast.querySelector('.btn-undo');
    if (undoBtn) {
        undoBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            clearTimeout(timer);
            removeToast();
            if (typeof onUndo === 'function') {
                try {
                    await onUndo();
                    showToast("Đã hoàn tác thao tác xóa thành công!", "success");
                } catch (err) {
                    console.error("Lỗi khi hoàn tác:", err);
                    showToast("Không thể hoàn tác thao tác xóa!", "error");
                }
            }
        });
    }

    // Nút đóng
    const closeBtn = toast.querySelector('.btn-close-undo');
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            clearTimeout(timer);
            removeToast();
        });
    }
}

// 3. HỘP XÁC NHẬN CHUYÊN NGHIỆP THAY THẾ window.confirm()
function showConfirmDialog(options) {
    return new Promise((resolve) => {
        const {
            title = 'Xác nhận thao tác',
            message = 'Bạn có chắc chắn muốn thực hiện hành động này?',
            confirmText = 'Xác nhận',
            cancelText = 'Hủy bỏ',
            type = 'danger' // 'danger' | 'primary' | 'warning'
        } = options;

        const overlayId = 'app-confirm-dialog-overlay';
        let overlay = document.getElementById(overlayId);
        if (overlay) overlay.remove();

        overlay = document.createElement('div');
        overlay.id = overlayId;
        overlay.className = 'fixed inset-0 z-[99999] bg-slate-950/60 backdrop-blur-xs flex items-center justify-center p-4 transition-all duration-200 opacity-0';

        let iconColor = 'bg-rose-50 text-rose-600 border-rose-200';
        let btnConfirmClass = 'bg-rose-600 hover:bg-rose-700 text-white';
        let iconName = 'fa-triangle-exclamation';

        if (type === 'primary') {
            iconColor = 'bg-amber-100 text-amber-600 border-amber-200';
            btnConfirmClass = 'bg-amber-500 hover:bg-amber-600 text-white font-bold';
            iconName = 'fa-circle-question';
        } else if (type === 'warning') {
            iconColor = 'bg-amber-100 text-amber-700 border-amber-200';
            btnConfirmClass = 'bg-amber-600 hover:bg-amber-700 text-white font-bold';
            iconName = 'fa-triangle-exclamation';
        }

        overlay.innerHTML = `
            <div class="bg-white rounded-2xl shadow-2xl border border-slate-200 max-w-sm w-full p-6 text-center transform scale-95 transition-all duration-200 space-y-4">
                <div class="w-12 h-12 rounded-2xl ${iconColor} border flex items-center justify-center mx-auto text-xl shadow-xs">
                    <i class="fa-solid ${iconName}"></i>
                </div>
                <div class="space-y-1">
                    <h3 class="text-sm font-bold text-slate-900">${title}</h3>
                    <p class="text-xs text-slate-600 leading-relaxed">${message}</p>
                </div>
                <div class="grid grid-cols-2 gap-2 pt-2">
                    <button type="button" id="btn-dialog-cancel"
                        class="w-full py-2.5 px-3 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold transition active:scale-[0.98]">
                        ${cancelText}
                    </button>
                    <button type="button" id="btn-dialog-confirm"
                        class="w-full py-2.5 px-3 rounded-xl ${btnConfirmClass} text-xs font-bold shadow-xs transition active:scale-[0.98]">
                        ${confirmText}
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        requestAnimationFrame(() => {
            overlay.classList.remove('opacity-0');
            overlay.classList.add('opacity-100');
            const card = overlay.firstElementChild;
            if (card) {
                card.classList.remove('scale-95');
                card.classList.add('scale-100');
            }
        });

        const closeDialog = (result) => {
            overlay.classList.remove('opacity-100');
            overlay.classList.add('opacity-0');
            setTimeout(() => {
                overlay.remove();
                resolve(result);
            }, 150);
        };

        const cancelBtn = overlay.querySelector('#btn-dialog-cancel');
        const confirmBtn = overlay.querySelector('#btn-dialog-confirm');

        if (cancelBtn) cancelBtn.onclick = () => closeDialog(false);
        if (confirmBtn) confirmBtn.onclick = () => closeDialog(true);

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeDialog(false);
        });

        // Hỗ trợ bấm phím Escape
        const handleKeyDown = (e) => {
            if (e.key === 'Escape') {
                window.removeEventListener('keydown', handleKeyDown);
                closeDialog(false);
            }
        };
        window.addEventListener('keydown', handleKeyDown);
    });
}

// 4. SKELETON LOADERS
function renderSkeletonCards(containerId, count = 2) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = Array(count).fill(0).map(() => `
        <div class="bg-white border border-slate-200 rounded-2xl p-4 space-y-3">
            <div class="flex items-center gap-3">
                <div class="skeleton-box w-10 h-10 rounded-xl shrink-0"></div>
                <div class="space-y-1.5 flex-1">
                    <div class="skeleton-box h-3.5 w-1/2"></div>
                    <div class="skeleton-box h-2.5 w-1/3"></div>
                </div>
            </div>
            <div class="skeleton-box h-12 w-full rounded-xl"></div>
            <div class="flex justify-between items-center pt-2">
                <div class="skeleton-box h-3 w-20"></div>
                <div class="skeleton-box h-7 w-24 rounded-lg"></div>
            </div>
        </div>
    `).join('');
}

// 5. EMPTY STATES VỚI HƯỚNG DẪN HÀNH ĐỘNG
function renderEmptyState(options) {
    const {
        icon = 'fa-calendar-xmark',
        title = 'Chưa có dữ liệu',
        description = 'Hiện tại chưa có mục nào trong danh sách.',
        actionText = '+ Thêm mới',
        actionOnClick = ''
    } = options;

    return `
        <div class="empty-state-card my-4">
            <div class="w-14 h-14 rounded-2xl bg-amber-50 text-amber-600 border border-amber-200/60 flex items-center justify-center text-2xl shadow-xs mb-3">
                <i class="fa-solid ${icon}"></i>
            </div>
            <h4 class="text-sm font-bold text-slate-800 mb-1">${title}</h4>
            <p class="text-xs text-slate-500 max-w-xs mb-4 leading-relaxed">${description}</p>
            ${actionText && actionOnClick ? `
                <button type="button" onclick="${actionOnClick}"
                    class="btn-primary">
                    ${actionText}
                </button>
            ` : ''}
        </div>
    `;
}


// 6. GLOBAL REPLACEMENT CHO window.alert() BẰNG TOAST THÔNG BÁO HIỆN ĐẠI
window.alert = function(msg) {
    if (!msg) return;
    const text = String(msg);
    const isSuccess = /thành công|hoàn tất|đã lưu|đã thêm|đã xóa|đã cập nhật|hợp lệ/i.test(text);
    const isError = /lỗi|thất bại|không thể|không được|chưa|sai|trùng|bắt buộc/i.test(text);
    showToast(text, isSuccess ? 'success' : (isError ? 'error' : 'warning'));
};
