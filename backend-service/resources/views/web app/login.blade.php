<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>بوابة نيوبارك الرقمية - تسجيل الدخول</title>

    <link rel="icon" type="image/png" href="{{ asset('logo.png') }}">
    <link href="https://fonts.googleapis.com/css2?family=Almarai:wght@300;400;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <style>
        body { 
            font-family: 'Almarai', sans-serif; 
            -webkit-tap-highlight-color: transparent;
        }
        .bg-newpark-light { background-color: #F5F7FA; }
        .text-newpark-blue { color: #1947C9; }
        .bg-newpark-blue { background-color: #1947C9; }
        
        /* تحسين الأنيميشن ليكون أخف على الموبايل */
        .fade-in-up { 
            animation: fadeInUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards; 
        }
        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(15px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* منع مشاكل الـ Zoom التلقائي في الآيفون عند التركيز على Input */
        @media screen and (max-width: 768px) {
            input { font-size: 16px !important; }
        }
    </style>
</head>
<body class="bg-newpark-light min-h-screen flex items-center justify-center relative overflow-hidden">

    <div class="absolute -top-24 -left-24 w-48 h-48 md:w-64 md:h-64 bg-[#1947C9] opacity-[0.06] rounded-full"></div>
    <div class="absolute -bottom-24 -right-24 w-64 h-64 md:w-80 md:h-80 bg-[#38BDF8] opacity-[0.1] rounded-full"></div>

    <div class="fade-in-up w-full max-w-[420px] bg-white p-6 sm:p-8 md:p-10 rounded-[24px] md:rounded-[32px] border border-gray-100 shadow-[0_20px_50px_rgba(0,0,0,0.04)] z-10 mx-4">
        
        <div class="flex justify-center md:justify-start mb-8">
            <div class="h-1.5 w-12 bg-newpark-blue rounded-full"></div>
        </div>

        <div class="mb-8 text-center md:text-right">
            <p class="text-newpark-blue font-bold text-sm mb-1 uppercase tracking-wider">New Park Digital</p>
            <h1 class="text-gray-800 text-xl md:text-2xl font-extrabold mb-2">بوابة الموظفين الرقمية</h1>
            <p class="text-gray-400 text-xs md:text-sm">يرجى إدخال بيانات الاعتماد للوصول لحسابك</p>
        </div>

        <form id="loginForm" class="space-y-5">
            <div class="group">
                <label class="block text-xs font-bold text-gray-500 mb-2 mr-1">اسم المستخدم</label>
                <div class="relative">
                    <span class="absolute inset-y-0 right-4 flex items-center text-gray-400 group-focus-within:text-newpark-blue transition-colors">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                        </svg>
                    </span>
                    <input type="text" id="user_name" name="user_name" required
                           class="w-full bg-gray-50 border-gray-100 border-2 rounded-2xl py-4 pr-12 pl-4 focus:outline-none focus:border-newpark-blue focus:bg-white transition-all text-sm placeholder-gray-300"
                           placeholder="UserName">
                </div>
            </div>

            <div class="group">
                <label class="block text-xs font-bold text-gray-500 mb-2 mr-1">كلمة المرور</label>
                <div class="relative">
                    <span class="absolute inset-y-0 right-4 flex items-center text-gray-400 group-focus-within:text-newpark-blue transition-colors">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                        </svg>
                    </span>
                    <input type="password" id="password" name="password" required
                           class="w-full bg-gray-50 border-gray-100 border-2 rounded-2xl py-4 pr-12 pl-4 focus:outline-none focus:border-newpark-blue focus:bg-white transition-all text-sm placeholder-gray-300"
                           placeholder="••••••••">
                </div>
            </div>

            <div class="pt-2">
                <button type="submit" id="submitBtn"
                        class="w-full bg-newpark-blue text-white font-bold py-4.5 py-4 rounded-2xl shadow-[0_10px_20px_rgba(25,71,201,0.2)] hover:shadow-[0_15px_25px_rgba(25,71,201,0.3)] active:scale-[0.97] transition-all flex items-center justify-center space-x-2 text-base">
                    <span id="btnText">دخول للمنصة</span>
                    <svg id="loader" class="hidden animate-spin h-5 w-5 text-white mr-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                </button>
            </div>
        </form>

        <div class="mt-8 pt-6 border-t border-gray-50 flex flex-col items-center space-y-3">
            <p class="text-gray-400 text-[10px]">جميع الحقوق محفوظة © نيوبارك 2026</p>
        </div>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', async function(e) {
            e.preventDefault();

            const submitBtn = document.getElementById('submitBtn');
            const btnText = document.getElementById('btnText');
            const loader = document.getElementById('loader');
            const user_name = document.getElementById('user_name').value;
            const password = document.getElementById('password').value;

            submitBtn.disabled = true;
            btnText.innerText = 'جاري التحقق...';
            loader.classList.remove('hidden');

            try {
                const response = await fetch('https://hr.dairypark.co/api/login', {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        user_name: user_name,
                        password: password
                    })
                });

                const data = await response.json();

                if (response.status === 200) {
                    localStorage.setItem('token', data.token);
                    localStorage.setItem('user_data', JSON.stringify(data.user));
                    window.location.href = '/dashboard'; 
                } else {
                    Swal.fire({
                        icon: 'error',
                        title: 'عذراً..',
                        text: data.message || 'خطأ في بيانات الدخول، يرجى المحاولة ثانية.',
                        confirmButtonText: 'حاول مجدداً',
                        confirmButtonColor: '#1947C9',
                        customClass: { popup: 'rounded-[20px]', confirmButton: 'rounded-xl px-8' }
                    });
                }
            } catch (error) {
                Swal.fire({
                    icon: 'warning',
                    title: 'مشكلة في الاتصال',
                    text: 'تأكد من اتصالك بالإنترنت وحاول مرة أخرى.',
                    confirmButtonText: 'موافق',
                    confirmButtonColor: '#1947C9',
                    customClass: { popup: 'rounded-[20px]' }
                });
            } finally {
                submitBtn.disabled = false;
                btnText.innerText = 'دخول للمنصة';
                loader.classList.add('hidden');
            }
        });
    </script>
</body>
</html>