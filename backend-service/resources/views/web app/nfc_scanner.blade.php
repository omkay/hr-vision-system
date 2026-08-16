<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>نيوبارك - بوابة المسح الذكي</title>

    <link rel="icon" type="image/png" href="{{ asset('logo.png') }}">
    <link href="https://fonts.googleapis.com/css2?family=Almarai:wght@300;400;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    
    <style>
        body { 
            font-family: 'Almarai', sans-serif; 
            background-color: #F8FAFC; 
            overflow: hidden;
            -webkit-tap-highlight-color: transparent;
        }
        .text-newpark-blue { color: #1947C9; }
        .bg-newpark-blue { background-color: #1947C9; }
        
        /* أنيميشن الرادار عند تفعيل الحساس */
        .pulse-scanner { 
            animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; 
        }
        @keyframes pulse {
            0% { transform: scale(0.8); opacity: 0.5; }
            100% { transform: scale(1.5); opacity: 0; }
        }

        /* دخول العناصر بنعومة */
        .fade-in { animation: fadeIn 0.6s ease-out forwards; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        
        /* تصميم مخصص لـ SweetAlert ليناسب الهوية */
        .custom-swal-popup { border-radius: 32px !important; padding: 2rem !important; }
    </style>
</head>
<body class="min-h-screen flex flex-col bg-slate-50">

    <nav class="bg-white/70 backdrop-blur-md border-b border-gray-100 px-4 md:px-8 py-4 flex items-center sticky top-0 z-30">
        <button onclick="window.location.href='/dashboard'" class="p-2 ml-3 bg-gray-50 text-gray-400 hover:text-newpark-blue rounded-xl transition-all active:scale-90">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
        </button>
        <div class="flex-1">
            <h1 class="text-gray-800 font-extrabold text-sm md:text-base leading-none">بوابة المسح الذكي</h1>
            <p id="transactionName" class="text-newpark-blue text-[10px] md:text-xs font-bold mt-1 uppercase tracking-tighter opacity-70">جاري التحميل...</p>
        </div>
    </nav>

    <div class="flex-1 flex flex-col items-center justify-center px-6 fade-in">
        
        <div class="relative flex items-center justify-center mb-12">
            <div id="scannerPulse" class="absolute w-48 h-48 md:w-60 md:h-60 bg-blue-400/20 rounded-full pulse-scanner hidden"></div>
            <div id="scannerPulse2" class="absolute w-48 h-48 md:w-60 md:h-60 bg-blue-300/10 rounded-full pulse-scanner hidden" style="animation-delay: 0.5s"></div>
            
            <div class="relative bg-white p-12 md:p-16 rounded-[60px] shadow-[0_30px_60px_-15px_rgba(25,71,201,0.1)] border border-white z-10 transition-all duration-500" id="mainBox">
                <div id="nfcIconContainer" class="text-gray-100 transition-colors duration-700">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-24 w-24 md:h-32 md:w-32" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.2" d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm12 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z" />
                    </svg>
                </div>
            </div>
        </div>

        <div id="statusContainer" class="text-center max-w-xs transition-all duration-500">
            <h3 id="statusTitle" class="text-xl md:text-2xl font-black text-gray-800 mb-3">جاهز للمسح</h3>
            <p id="statusDesc" class="text-gray-400 text-xs md:text-sm leading-relaxed px-4">اضغط على الزر أدناه لتفعيل مستشعر الـ NFC وبدء استقبال الموظفين</p>
        </div>

        <div class="mt-14 w-full max-w-[280px]">
            <button id="startScanBtn" class="w-full bg-newpark-blue text-white py-5 rounded-[24px] font-bold shadow-[0_15px_30px_rgba(25,71,201,0.2)] active:scale-95 active:shadow-inner transition-all text-lg flex items-center justify-center gap-3">
                <span>تفعيل الحساس</span>
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clip-rule="evenodd" />
                </svg>
            </button>
        </div>
    </div>

    <div class="py-6 text-center">
        <p class="text-[10px] text-gray-300 uppercase tracking-widest">New Park NFC Infrastructure v2.0</p>
    </div>

    <script>
        const token = localStorage.getItem('token');
        const transId = sessionStorage.getItem('selected_transaction_id');
        const transName = sessionStorage.getItem('selected_transaction_name');

        if (!token || !transId) { window.location.href = '/dashboard'; }
        document.getElementById('transactionName').innerText = transName;

        // نظام الأصوات لتعزيز التجربة (Feedback)
        function playAudioFeedback(type) {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = audioCtx.createOscillator();
            const gainNode = audioCtx.createGain();
            oscillator.connect(gainNode);
            gainNode.connect(audioCtx.destination);

            if (type === 'success') {
                oscillator.frequency.setValueAtTime(880, audioCtx.currentTime); 
                gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime);
                oscillator.start();
                oscillator.stop(audioCtx.currentTime + 0.1);
            } else {
                oscillator.type = 'square';
                oscillator.frequency.setValueAtTime(110, audioCtx.currentTime);
                gainNode.gain.setValueAtTime(0.05, audioCtx.currentTime);
                oscillator.start();
                oscillator.stop(audioCtx.currentTime + 0.2);
            }
        }

        const startBtn = document.getElementById('startScanBtn');
        const iconCont = document.getElementById('nfcIconContainer');
        const pulse1 = document.getElementById('scannerPulse');
        const pulse2 = document.getElementById('scannerPulse2');
        const statusTitle = document.getElementById('statusTitle');
        const statusDesc = document.getElementById('statusDesc');

        startBtn.addEventListener('click', async () => {
            if ('NDEFReader' in window) {
                try {
                    const ndef = new NDEFReader();
                    await ndef.scan();
                    
                    // تحويل الواجهة لوضع "الاستماع"
                    startBtn.parentElement.classList.add('opacity-0', 'pointer-events-none');
                    pulse1.classList.remove('hidden');
                    pulse2.classList.remove('hidden');
                    iconCont.classList.replace('text-gray-100', 'text-newpark-blue');
                    statusTitle.innerText = "في انتظار البطاقة...";
                    statusTitle.classList.add('text-newpark-blue');
                    statusDesc.innerText = "مرر البطاقة خلف الهاتف الآن";

                    ndef.onreading = event => {
                        const cardId = event.serialNumber.toUpperCase();
                        processReceipt(cardId);
                    };

                } catch (error) {
                    Swal.fire({ icon: 'error', title: 'تنبيه', text: 'يرجى تفعيل الـ NFC من إعدادات الهاتف أولاً.', confirmButtonColor: '#1947C9' });
                }
            } else {
                Swal.fire({ icon: 'warning', title: 'المتصفح غير مدعوم', text: 'يرجى استخدام متصفح Chrome على نظام Android لتشغيل هذه الميزة.', confirmButtonColor: '#1947C9' });
            }
        });

        async function processReceipt(cardId) {
            statusTitle.innerText = "جاري التحقق...";
            
            try {
                const response = await fetch('https://hr.dairypark.co/api/receipt/add', {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({ card_id: cardId, transaction_id: transId })
                });

                const data = await response.json();

                if (response.status === 200) {
                    playAudioFeedback('success');
                    const emp = data.employee.received_by;
                    
                    Swal.fire({
                        icon: 'success',
                        title: 'تم التسجيل',
                        html: `
                            <div class="mt-4 space-y-2 text-right">
                                <div class="bg-gray-50 p-4 rounded-2xl border border-gray-100">
                                    <p class="text-xs text-gray-400 mb-1">اسم الموظف</p>
                                    <p class="text-sm font-bold text-gray-800">${emp.name}</p>
                                </div>
                                <div class="grid grid-cols-2 gap-2">
                                    <div class="bg-gray-50 p-3 rounded-2xl border border-gray-100">
                                        <p class="text-[10px] text-gray-400 mb-1">الرقم الوظيفي</p>
                                        <p class="text-xs font-bold text-gray-800">${emp.job_num}</p>
                                    </div>
                                    <div class="bg-gray-50 p-3 rounded-2xl border border-gray-100">
                                        <p class="text-[10px] text-gray-400 mb-1">القسم</p>
                                        <p class="text-xs font-bold text-gray-800">${emp.department || 'عام'}</p>
                                    </div>
                                </div>
                            </div>
                        `,
                        timer: 2500,
                        showConfirmButton: false,
                        customClass: { popup: 'custom-swal-popup' }
                    });
                } else {
                    playAudioFeedback('error');
                    Swal.fire({ icon: 'info', title: 'ملاحظة', text: data.message, confirmButtonColor: '#1947C9' });
                }
            } catch (error) {
                playAudioFeedback('error');
                Swal.fire({ icon: 'error', title: 'خطأ في الشبكة', text: 'تأكد من اتصالك بالسيرفر', confirmButtonColor: '#1947C9' });
            } finally {
                statusTitle.innerText = "في انتظار البطاقة...";
            }
        }
    </script>
</body>
</html>