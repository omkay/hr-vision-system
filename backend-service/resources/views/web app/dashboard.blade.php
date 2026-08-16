<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>بوابة نيوبارك - العمليات</title>

    <link rel="icon" type="image/png" href="{{ asset('logo.png') }}">
    <link href="https://fonts.googleapis.com/css2?family=Almarai:wght@300;400;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <style>
        body { 
            font-family: 'Almarai', sans-serif; 
            background-color: #F8FAFC; 
            -webkit-tap-highlight-color: transparent;
        }
        .text-newpark-blue { color: #1947C9; }
        .bg-newpark-blue { background-color: #1947C9; }
        
        /* تأثير التحميل الذكي */
        .card-shimmer { 
            animation: shimmer 1.5s infinite linear; 
            background: linear-gradient(to right, #f6f7f8 0%, #edeef1 20%, #f6f7f8 40%, #f6f7f8 100%); 
            background-size: 800px 104px; 
        }
        @keyframes shimmer { 0% { background-position: -468px 0; } 100% { background-position: 468px 0; } }

        /* تحسين مظهر السكرول بار */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #f1f1f1; }
        ::-webkit-scrollbar-thumb { background: #1947C9; border-radius: 10px; }
    </style>
</head>
<body class="min-h-screen pb-10">

    <nav class="bg-white/80 backdrop-blur-md shadow-sm border-b border-gray-100 px-4 md:px-8 py-3 md:py-4 flex justify-between items-center mb-6 md:mb-8 sticky top-0 z-50">
        <div class="flex items-center space-x-2 md:space-x-3 space-x-reverse">
            <div class="w-9 h-9 md:w-10 md:h-10 bg-newpark-blue rounded-xl flex items-center justify-center text-white font-bold shadow-lg shadow-blue-200 text-lg">N</div>
            <div class="flex flex-col">
                <h1 class="text-gray-800 font-extrabold text-sm md:text-lg leading-tight">نيوبارك <span class="text-newpark-blue">HR</span></h1>
                <span class="text-[10px] text-gray-400 hidden md:block tracking-widest uppercase">Digital Portal</span>
            </div>
        </div>
        
        <div class="flex items-center gap-2 md:gap-4">
            <div class="hidden sm:flex flex-col items-end">
                <span id="userNameDisplay" class="text-gray-700 text-xs md:text-sm font-bold"></span>
                <span class="text-[10px] text-green-500 font-bold">متصل الآن</span>
            </div>
            <div class="sm:hidden w-8 h-8 bg-gray-100 rounded-full flex items-center justify-center text-gray-500">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clip-rule="evenodd" />
                </svg>
            </div>
            <div class="h-8 w-[1px] bg-gray-100 mx-1"></div>
            <button onclick="logout()" class="flex items-center text-red-500 hover:bg-red-50 p-2 rounded-xl transition-all active:scale-95">
                <span class="ml-2 text-xs md:text-sm font-bold hidden xs:block">خروج</span>
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                </svg>
            </button>
        </div>
    </nav>

    <div class="max-w-5xl mx-auto px-4 md:px-6">
        <div class="mb-8 md:mb-10 text-right">
            <h2 class="text-xl md:text-2xl font-extrabold text-gray-800">قائمة العمليات المتاحة</h2>
            <p class="text-gray-500 text-xs md:text-sm mt-1 leading-relaxed">اختر الدفعة المطلوبة للبدء بمسح بطاقات NFC المخصصة للموظفين</p>
        </div>

        <div id="transactionsList" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 gap-4 md:gap-6">
            <div class="card-shimmer h-28 rounded-[24px]"></div>
            <div class="card-shimmer h-28 rounded-[24px]"></div>
        </div>
    </div>

    <script>
        const token = localStorage.getItem('token');
        const userData = JSON.parse(localStorage.getItem('user_data') || '{}');

        if (!token) {
            window.location.href = '/login';
        } else {
            document.getElementById('userNameDisplay').innerText = userData.user_name || 'مستخدم نيوبارك';
        }

        async function fetchTransactions() {
            const listContainer = document.getElementById('transactionsList');

            try {
                const response = await fetch('https://hr.dairypark.co/api/transactions/get', {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    }
                });

                if (response.status === 401) { logout(); return; }

                const data = await response.json();
                listContainer.innerHTML = ''; 

                if (data.transaction && data.transaction.length > 0) {
                    data.transaction.forEach(item => {
                        listContainer.innerHTML += `
                            <button onclick="goToNfcScanner(${item.id}, '${item.name}')" 
                                    class="bg-white p-5 md:p-6 rounded-[24px] border border-gray-100 shadow-sm hover:shadow-xl hover:border-newpark-blue transition-all flex justify-between items-center group text-right active:scale-[0.98] relative overflow-hidden">
                                <div class="absolute top-0 right-0 w-1 h-full bg-newpark-blue opacity-0 group-hover:opacity-100 transition-opacity"></div>
                                <div class="z-10">
                                    <div class="flex items-center mb-2">
                                        <div class="w-2 h-2 bg-green-500 rounded-full ml-2 animate-pulse"></div>
                                        <h3 class="font-extrabold text-gray-800 group-hover:text-newpark-blue transition-colors text-sm md:text-base">${item.name}</h3>
                                    </div>
                                    <div class="flex items-center text-[10px] md:text-xs text-gray-400">
                                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 ml-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                                        </svg>
                                        ${item.created_at}
                                    </div>
                                </div>
                                <div class="bg-gray-50 p-3 rounded-2xl text-gray-400 group-hover:bg-newpark-blue group-hover:text-white transition-all shadow-inner group-hover:shadow-blue-200">
                                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 md:h-6 md:w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                                    </svg>
                                </div>
                            </button>
                        `;
                    });
                } else {
                    listContainer.innerHTML = `
                        <div class="col-span-full flex flex-col items-center py-20 bg-white rounded-[32px] border border-dashed border-gray-200">
                            <div class="bg-gray-50 p-5 rounded-full mb-4">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-10 w-10 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                                </svg>
                            </div>
                            <p class="text-gray-400 text-sm">لا يوجد عمليات مفتوحة حالياً</p>
                        </div>
                    `;
                }
            } catch (error) {
                Swal.fire({ icon: 'error', title: 'خطأ في الاتصال', text: 'تعذر جلب البيانات، يرجى التحقق من السيرفر', confirmButtonColor: '#1947C9' });
            }
        }

        function goToNfcScanner(id, name) {
            sessionStorage.setItem('selected_transaction_id', id);
            sessionStorage.setItem('selected_transaction_name', name);
            window.location.href = '/nfc-scanner'; 
        }

        async function logout() {
            try {
                await fetch('https://hr.dairypark.co/api/logout', {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                        'Authorization': `Bearer ${token}`
                    }
                });
            } catch (e) { } 
            finally {
                localStorage.clear();
                sessionStorage.clear();
                window.location.href = '/login';
            }
        }

        fetchTransactions();
    </script>
</body>
</html>