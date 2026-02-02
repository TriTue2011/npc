// Data Management Module
class DataManager {
    constructor() {
        this.monthlyData = null;
        this.dailyData = null;
        this.currentAccount = null;
        this.currentYear = new Date().getFullYear();
        // Cấu hình chu kỳ thanh toán theo tài khoản
        this.billingCycles = {
            // Default: đầu tháng đến cuối tháng
            default: { startDay: 1, type: 'calendar' },
            // Ví dụ các chu kỳ khác (có thể cấu hình qua UI):
            'PE0500123456': { startDay: 15, type: 'cycle' }, // Từ ngày 15 hàng tháng
            'PE0600789012': { startDay: 20, type: 'cycle' }, // Từ ngày 20 hàng tháng
            'PE0700345678': { startDay: 10, type: 'cycle' }, // Từ ngày 10 hàng tháng
        };

        // Load saved billing cycles từ localStorage
        this.loadBillingCycles();
    }

    // Load danh sách accounts từ options.json
    async loadAccounts() {
        try {
            const baseUrl = this.getBaseUrl();
            const response = await fetch(baseUrl + '/api/npc/options');

            if (!response.ok) {
                throw new Error('Không thể tải danh sách tài khoản từ API');
            }

            const options = await response.json();
            const accounts = JSON.parse(options.accounts_json);
            return accounts;
        } catch (error) {
            console.error('Lỗi tải danh sách tài khoản:', error);
            throw error;
        }
    }

    // Load dữ liệu cho một tài khoản cụ thể
    async loadDataForAccount(account) {
        try {
            const baseUrl = this.getBaseUrl();

            // Load monthly data
            const monthlyResponse = await fetch(`${baseUrl}/api/npc/monthly/${account}`);
            if (!monthlyResponse.ok) {
                throw new Error(`Không thể tải dữ liệu hóa đơn cho ${account}`);
            }
            this.monthlyData = await monthlyResponse.json();
            console.log('📊 Monthly data loaded:', this.monthlyData);

            // Load daily data
            const dailyResponse = await fetch(`${baseUrl}/api/npc/daily/${account}`);
            if (!dailyResponse.ok) {
                throw new Error(`Không thể tải dữ liệu tiêu thụ cho ${account}`);
            }
            this.dailyData = await dailyResponse.json();
            console.log('📅 Daily data loaded:', this.dailyData?.length, 'records');

            this.currentAccount = account;
            this.processData();
            console.log('✅ Data processed. Monthly:', this.monthlyData?.SanLuong?.length, 'months, Daily:', this.dailyData?.length, 'days');

            return {
                monthlyData: this.monthlyData,
                dailyData: this.dailyData
            };
        } catch (error) {
            console.error('Lỗi tải dữ liệu:', error);
            throw error;
        }
    }

    // Xử lý và chuẩn hóa dữ liệu
    processData() {
        // Xử lý daily data
        if (this.dailyData && Array.isArray(this.dailyData)) {
            this.dailyData.forEach(day => {
                if (day["Điện tiêu thụ (kWh)"] !== "Không có dữ liệu") {
                    const value = day["Điện tiêu thụ (kWh)"];
                    if (typeof value === 'string') {
                        day["Điện tiêu thụ (kWh)"] = parseFloat(value.replace(',', '.')) || 0;
                    } else {
                        day["Điện tiêu thụ (kWh)"] = parseFloat(value) || 0;
                    }
                } else {
                    day["Điện tiêu thụ (kWh)"] = 0;
                }
            });

            // Sắp xếp dữ liệu theo thứ tự thời gian
            this.dailyData.sort((a, b) =>
                new Date(a.Ngày.split('-').reverse().join('-')) -
                new Date(b.Ngày.split('-').reverse().join('-'))
            );
        } else {
            this.dailyData = [];
        }

        // Sắp xếp monthly data
        if (this.monthlyData && this.monthlyData.SanLuong && Array.isArray(this.monthlyData.SanLuong)) {
            this.monthlyData.SanLuong.sort((a, b) => a.Tháng - b.Tháng);
        } else if (this.monthlyData) {
            this.monthlyData.SanLuong = [];
        }
        
        if (this.monthlyData && this.monthlyData.TienDien && Array.isArray(this.monthlyData.TienDien)) {
            this.monthlyData.TienDien.sort((a, b) => a.Tháng - b.Tháng);
        } else if (this.monthlyData) {
            this.monthlyData.TienDien = [];
        }
        
        // Đảm bảo monthlyData có structure đúng
        if (!this.monthlyData) {
            this.monthlyData = { SanLuong: [], TienDien: [] };
        }
    }    // Lấy dữ liệu theo tháng (hỗ trợ chu kỳ thanh toán)
    getDataByMonth(monthYear) {
        const billingCycle = this.getBillingCycle();

        if (billingCycle.type === 'calendar') {
            // Chu kỳ theo tháng dương lịch (cũ)
            return this.dailyData.filter(day =>
                day.Ngày && day.Ngày.slice(3, 10) === monthYear
            );
        } else if (billingCycle.type === 'cycle' && billingCycle.startDay === 1) {
            // Chu kỳ được cấu hình thủ công từ ngày 1 - xử lý như tháng dương lịch
            return this.dailyData.filter(day =>
                day.Ngày && day.Ngày.slice(3, 10) === monthYear
            );
        } else {
            // Chu kỳ thanh toán tùy chỉnh
            return this.getDataByBillingPeriod(monthYear, billingCycle.startDay);
        }
    }    // Lấy cấu hình chu kỳ thanh toán cho tài khoản hiện tại
    getBillingCycle() {
        const cycle = this.billingCycles[this.currentAccount] || this.billingCycles.default;
        console.log('🔍 getBillingCycle:', { currentAccount: this.currentAccount, cycle });
        return cycle;
    }

    // Tính ngày đầu kỳ, cuối kỳ theo logic đúng từ NPC
    tinhngaydauky(ngaydauky, today = null) {
        if (today === null) {
            today = new Date();
        }

        const day = today.getDate();
        const month = today.getMonth(); // 0-based (0 = January)
        const year = today.getFullYear();

        let start;

        if (ngaydauky === 1) {
            // Chu kỳ theo tháng dương lịch
            start = new Date(year, month, 1);
        } else {
            // Chu kỳ tùy chỉnh
            if (day < ngaydauky) {
                // Nếu ngày hiện tại < ngày đầu kỳ, lấy tháng trước
                if (month === 0) {
                    // Tháng 1, lùi về tháng 12 năm trước
                    start = new Date(year - 1, 11, ngaydauky);
                } else {
                    start = new Date(year, month - 1, ngaydauky);
                }
            } else {
                // Nếu ngày hiện tại >= ngày đầu kỳ, lấy tháng hiện tại
                start = new Date(year, month, ngaydauky);
            }
        }

        const end = new Date(today);

        // Tính ngày kết thúc kỳ
        let next_month = start.getMonth() + 1;
        let next_year = start.getFullYear();

        if (next_month > 11) {
            next_month = 0;
            next_year += 1;
        }

        let next_start;
        try {
            next_start = new Date(next_year, next_month, ngaydauky);
        } catch (error) {
            // Nếu ngày không hợp lệ (ví dụ: 31/2), lấy ngày cuối tháng
            const lastDayNextMonth = new Date(next_year, next_month + 1, 0).getDate();
            next_start = new Date(next_year, next_month, Math.min(ngaydauky, lastDayNextMonth));
        }

        const end_ky = new Date(next_start.getTime() - 24 * 60 * 60 * 1000); // Trừ 1 ngày
        const prev_end_ky = new Date(start.getTime() - 24 * 60 * 60 * 1000); // Trừ 1 ngày

        return {
            start: start,
            end: end,
            end_ky: end_ky,
            prev_end_ky: prev_end_ky
        };
    }    // Lấy dữ liệu theo chu kỳ thanh toán (từ ngày X tháng này đến ngày X-1 tháng sau)
    getDataByBillingPeriod(monthYear, startDay) {
        const [month, year] = monthYear.split('-').map(Number);

        // FIXED: Tính ngày bắt đầu thực tế của kỳ thanh toán        // monthYear là tháng hiển thị (tháng kết thúc kỳ)        // monthYear là tháng hiển thị (tháng kết thúc kỳ)
        // Cần tìm ngày bắt đầu kỳ để tính đúng
        const endDate = new Date(year, month - 1, startDay - 1); // Ngày kết thúc kỳ (tháng hiện tại)
        const startDate = new Date(year, month - 2, startDay); // Ngày bắt đầu kỳ (tháng trước)

        // Xử lý trường hợp tháng 1 (phải lùi về tháng 12 năm trước)
        if (month === 1) {
            startDate.setFullYear(year - 1);
            startDate.setMonth(11); // Tháng 12 (0-based)
        } const periods = {
            start: startDate,
            end_ky: endDate
        };
        const filteredData = this.dailyData.filter(day => {
            if (!day.Ngày) return false;

            // Chuyển đổi format ngày từ dd-mm-yyyy sang Date object
            const dayDate = new Date(day.Ngày.split('-').reverse().join('-'));

            // Normalize dates to avoid time comparison issues
            const dayDateNormalized = new Date(dayDate.getFullYear(), dayDate.getMonth(), dayDate.getDate());
            const startDateNormalized = new Date(periods.start.getFullYear(), periods.start.getMonth(), periods.start.getDate());
            const endDateNormalized = new Date(periods.end_ky.getFullYear(), periods.end_ky.getMonth(), periods.end_ky.getDate());

            // Kiểm tra xem ngày có nằm trong chu kỳ không
            const isInPeriod = dayDateNormalized >= startDateNormalized && dayDateNormalized <= endDateNormalized;

            return isInPeriod;
        });
        return filteredData;
    }

    // Lấy dữ liệu trong khoảng thời gian
    getDataByDateRange(startDate, endDate) {
        return this.dailyData.filter(day => {
            const dayDate = new Date(day.Ngày.split('-').reverse().join('-'));
            return dayDate >= startDate && dayDate <= endDate && day["Điện tiêu thụ (kWh)"] > 0;
        });
    }

    normalizeYearValue(value) {
        const year = parseInt(value, 10);
        return Number.isNaN(year) ? null : year;
    }

    buildBillingPeriodRange(month, year, billingCycle) {
        if (billingCycle.type === 'calendar' || (billingCycle.type === 'cycle' && billingCycle.startDay === 1)) {
            const start = new Date(year, month - 1, 1);
            const end = new Date(year, month, 0);
            return { start, end };
        }

        const start = new Date(year, month - 2, billingCycle.startDay);
        const end = new Date(year, month - 1, billingCycle.startDay - 1);

        return { start, end };
    }

    buildPeriodSummaryForMonth(aggregatedMonthlyData, month, year) {
        const billingCycle = this.getBillingCycle();
        const entry = aggregatedMonthlyData.SanLuong.find(item =>
            this.normalizeYearValue(item.Năm) === year && parseInt(item.Tháng, 10) === month
        );
        if (!entry) {
            return null;
        }

        const costEntry = aggregatedMonthlyData.TienDien.find(item =>
            this.normalizeYearValue(item.Năm) === year && parseInt(item.Tháng, 10) === month
        );
        const consumption = typeof entry["Điện tiêu thụ (KWh)"] === 'number'
            ? entry["Điện tiêu thụ (KWh)"]
            : parseFloat(entry["Điện tiêu thụ (KWh)"]) || 0;
        const cost = costEntry
            ? (typeof costEntry["Tiền Điện"] === 'number'
                ? costEntry["Tiền Điện"]
                : parseFloat(costEntry["Tiền Điện"]) || 0)
            : 0;
        const periodRange = this.buildBillingPeriodRange(month, year, billingCycle);

        return {
            month,
            year,
            consumption,
            cost,
            days: null,
            isCurrentPeriod: false,
            period: {
                start: periodRange.start,
                end: periodRange.end
            },
            details: {
                subtotal: null,
                tax: null
            }
        };
    }

    getMonthlyAggregation(filterYear = null) {
        const targetYear = this.normalizeYearValue(filterYear);
        const monthlyData = this.monthlyData || { SanLuong: [], TienDien: [] };
        const monthlyMap = new Map();

        // Aggregate from daily data first (covers all years)
        if (this.dailyData && Array.isArray(this.dailyData)) {
            this.dailyData.forEach(day => {
                if (!day.Ngày) return;
                const dayDate = new Date(day.Ngày.split('-').reverse().join('-'));
                if (Number.isNaN(dayDate.getTime())) return;

                const year = dayDate.getFullYear();
                if (targetYear !== null && year !== targetYear) return;

                const month = dayDate.getMonth() + 1;
                const key = `${year}-${month}`;
                const value = typeof day["Điện tiêu thụ (kWh)"] === 'number'
                    ? day["Điện tiêu thụ (kWh)"]
                    : parseFloat(day["Điện tiêu thụ (kWh)"]) || 0;

                const entry = monthlyMap.get(key) || {
                    Tháng: month,
                    Năm: year,
                    consumption: 0
                };

                entry.consumption += value;
                monthlyMap.set(key, entry);
            });
        }

        // Add monthly data if daily data missing for that month
        if (monthlyData.SanLuong && Array.isArray(monthlyData.SanLuong)) {
            monthlyData.SanLuong.forEach(item => {
                const year = this.normalizeYearValue(item.Năm);
                if (year === null) return;
                if (targetYear !== null && year !== targetYear) return;

                const month = parseInt(item.Tháng, 10);
                const key = `${year}-${month}`;
                if (!monthlyMap.has(key)) {
                    const consumption = typeof item["Điện tiêu thụ (KWh)"] === 'number'
                        ? item["Điện tiêu thụ (KWh)"]
                        : parseFloat(item["Điện tiêu thụ (KWh)"]) || 0;

                    monthlyMap.set(key, {
                        Tháng: month,
                        Năm: year,
                        consumption
                    });
                }
            });
        }

        const costMap = new Map();
        if (monthlyData.TienDien && Array.isArray(monthlyData.TienDien)) {
            monthlyData.TienDien.forEach(item => {
                const year = this.normalizeYearValue(item.Năm);
                if (year === null) return;
                if (targetYear !== null && year !== targetYear) return;

                const month = parseInt(item.Tháng, 10);
                const key = `${year}-${month}`;
                const cost = typeof item["Tiền Điện"] === 'number'
                    ? item["Tiền Điện"]
                    : parseFloat(item["Tiền Điện"]) || 0;
                costMap.set(key, cost);
            });
        }

        const sortedEntries = Array.from(monthlyMap.values()).sort((a, b) => {
            if (a.Năm !== b.Năm) {
                return a.Năm - b.Năm;
            }
            return a.Tháng - b.Tháng;
        });

        const SanLuong = [];
        const TienDien = [];

        sortedEntries.forEach(entry => {
            const key = `${entry.Năm}-${entry.Tháng}`;
            const monthlyCost = costMap.has(key)
                ? costMap.get(key)
                : (entry.consumption > 0 ? this.tinhTienDien(entry.consumption).total : 0);

            SanLuong.push({
                Tháng: entry.Tháng,
                Năm: entry.Năm,
                "Điện tiêu thụ (KWh)": entry.consumption
            });

            TienDien.push({
                Tháng: entry.Tháng,
                Năm: entry.Năm,
                "Tiền Điện": monthlyCost
            });
        });

        return { SanLuong, TienDien };
    }

    // Tính toán thống kê tổng quan (bao gồm kỳ hiện tại)
    calculateSummary(filterYear = null) {
        // Đảm bảo monthlyData có structure đúng
        if (!this.monthlyData) {
            this.monthlyData = { SanLuong: [], TienDien: [] };
        }
        if (!this.monthlyData.TienDien) {
            this.monthlyData.TienDien = [];
        }
        if (!this.monthlyData.SanLuong) {
            this.monthlyData.SanLuong = [];
        }
        
        // Đảm bảo dailyData là array
        if (!this.dailyData || !Array.isArray(this.dailyData)) {
            this.dailyData = [];
        }

        const aggregatedMonthlyData = this.getMonthlyAggregation(filterYear);
        let filteredTienDien = aggregatedMonthlyData.TienDien;
        let filteredSanLuong = aggregatedMonthlyData.SanLuong;
        let filteredDailyData = this.dailyData;

        const targetYear = this.normalizeYearValue(filterYear);

        if (targetYear !== null) {
            filteredDailyData = this.dailyData.filter(day => {
                if (!day.Ngày) return false;
                const year = parseInt(day.Ngày.split('-')[2]);
                return year === targetYear;
            });
        }
        
        // Tổng tiền điện
        const totalCost = filteredTienDien.reduce((sum, item) => {
            const value = item["Tiền Điện"] || 0;
            return sum + (typeof value === 'number' ? value : parseFloat(value) || 0);
        }, 0);

        // Trung bình hàng tháng
        const monthCount = filteredSanLuong.length;
        const avgMonthlyCost = monthCount > 0
            ? totalCost / monthCount
            : 0;

        // Tổng và trung bình tiêu thụ hàng tháng
        const totalMonthlyConsumption = filteredSanLuong.reduce((sum, item) => {
            const value = item["Điện tiêu thụ (KWh)"] || 0;
            return sum + (typeof value === 'number' ? value : parseFloat(value) || 0);
        }, 0);
        const avgMonthlyConsumption = monthCount > 0
            ? totalMonthlyConsumption / monthCount
            : 0;

        // Trung bình hàng ngày
        const validDailyData = filteredDailyData.filter(day => {
            const value = day["Điện tiêu thụ (kWh)"];
            return value && (typeof value === 'number' ? value > 0 : parseFloat(value) > 0);
        });
        const totalDailyConsumption = validDailyData.reduce((sum, day) => {
            const value = day["Điện tiêu thụ (kWh)"];
            return sum + (typeof value === 'number' ? value : parseFloat(value) || 0);
        }, 0);
        const avgDailyConsumption = validDailyData.length > 0
            ? totalDailyConsumption / validDailyData.length 
            : 0;

        // Tính toán kỳ hiện tại (chỉ khi đang xem năm hiện tại hoặc tất cả)
        const currentYear = new Date().getFullYear();
        const includeCurrentPeriod = targetYear === null || targetYear === currentYear;
        let currentPeriod = null;
        if (includeCurrentPeriod) {
            currentPeriod = this.calculateCurrentPeriod();
        }

        return {
            totalCost,
            avgMonthlyCost,
            avgMonthlyConsumption,
            avgDailyConsumption,
            totalMonthlyConsumption,
            currentPeriod // Thêm dữ liệu kỳ hiện tại
        };
    }    // Thiết lập chu kỳ thanh toán cho tài khoản
    setBillingCycle(account, startDay, type = 'cycle') {
        console.log('🔧 setBillingCycle called:', { account, startDay, type });
        this.billingCycles[account] = { startDay, type };
        console.log('🔧 Billing cycles after set:', this.billingCycles);
        // Lưu vào localStorage
        this.saveBillingCycles();
    }

    // Load billing cycles từ localStorage
    loadBillingCycles() {
        try {
            const saved = localStorage.getItem('evn_billing_cycles');
            if (saved) {
                const savedCycles = JSON.parse(saved);
                // Merge với default cycles, ưu tiên saved
                this.billingCycles = { ...this.billingCycles, ...savedCycles };
                console.log('Loaded billing cycles from localStorage:', this.billingCycles);
            }
        } catch (error) {
            console.error('Lỗi load billing cycles từ localStorage:', error);
        }
    }

    // Save billing cycles vào localStorage
    saveBillingCycles() {
        try {
            localStorage.setItem('evn_billing_cycles', JSON.stringify(this.billingCycles));
            console.log('Saved billing cycles to localStorage:', this.billingCycles);
        } catch (error) {
            console.error('Lỗi save billing cycles to localStorage:', error);
        }
    }    // Lấy thông tin chu kỳ thanh toán hiện tại
    getCurrentBillingInfo() {
        const cycle = this.getBillingCycle();
        if (cycle.type === 'calendar') {
            return {
                type: 'Theo tháng dương lịch',
                description: 'Từ đầu tháng đến cuối tháng'
            };
        } else if (cycle.type === 'cycle' && cycle.startDay === 1) {
            return {
                type: 'Theo chu kỳ thanh toán',
                description: 'Từ ngày 1 hàng tháng (tương đương tháng dương lịch)',
                startDay: cycle.startDay
            };
        } else {
            return {
                type: 'Theo chu kỳ thanh toán',
                description: `Từ ngày ${cycle.startDay} hàng tháng`,
                startDay: cycle.startDay
            };
        }
    }

    // Lấy base URL cho Ingress hoặc Static Path
    getBaseUrl() {
        const ingressMatch = window.location.pathname.match(/\/api\/hassio_ingress\/[^\/]+/);
        if (ingressMatch) return ingressMatch[0];

        const staticMatch = window.location.pathname.match(/\/npc-monitor/);
        if (staticMatch) return ''; // When served via static path, APIs should be relative to root

        return '';
    }    // Lấy các tháng duy nhất từ dữ liệu (hỗ trợ chu kỳ thanh toán)
    getUniqueMonths(filterYear = null) {
        const billingCycle = this.getBillingCycle();
        console.log('📅 getUniqueMonths - billing cycle:', billingCycle);
        console.log('📅 getUniqueMonths - filter year:', filterYear);

        // Đảm bảo dailyData là array
        if (!this.dailyData || !Array.isArray(this.dailyData) || this.dailyData.length === 0) {
            console.warn('⚠️ getUniqueMonths: No daily data available');
            // Nếu không có daily data, thử lấy từ monthly data
            if (this.monthlyData && this.monthlyData.SanLuong && this.monthlyData.SanLuong.length > 0) {
                let months = this.monthlyData.SanLuong.map(item => {
                    const month = item.Tháng.toString().padStart(2, '0');
                    const year = this.normalizeYearValue(item.Năm) || new Date().getFullYear();
                    return `${month}-${year}`;
                });
                
                // Lọc theo năm nếu có
                const targetYear = this.normalizeYearValue(filterYear);
                if (targetYear !== null) {
                    months = months.filter(m => m.endsWith(`-${targetYear}`));
                }
                
                return months.sort((a, b) => {
                    const [m1, y1] = a.split('-');
                    const [m2, y2] = b.split('-');
                    return new Date(y2, m2 - 1) - new Date(y1, m1 - 1);
                });
            }
            return [];
        }

        // Lọc dailyData theo năm nếu có
        let filteredDailyData = this.dailyData;
        const targetYear = this.normalizeYearValue(filterYear);
        if (targetYear !== null) {
            filteredDailyData = this.dailyData.filter(day => {
                if (!day.Ngày) return false;
                const year = parseInt(day.Ngày.split('-')[2]);
                return year === targetYear;
            });
        }

        if (billingCycle.type === 'calendar') {
            // Chu kỳ theo tháng dương lịch (cũ)
            const uniqueMonths = [...new Set(filteredDailyData.map(day => day.Ngày?.slice(3, 10)).filter(Boolean))];
            const result = uniqueMonths.sort((a, b) =>
                new Date(b.split('-').reverse().join('-')) -
                new Date(a.split('-').reverse().join('-'))
            );
            console.log('📅 Calendar type result:', result);
            return result;
        } else if (billingCycle.type === 'cycle' && billingCycle.startDay === 1) {
            // Chu kỳ được cấu hình thủ công từ ngày 1 - xử lý như tháng dương lịch nhưng với "Kỳ này"
            const uniqueMonths = [...new Set(filteredDailyData.map(day => day.Ngày.slice(3, 10)))];
            const sortedMonths = uniqueMonths.sort((a, b) =>
                new Date(b.split('-').reverse().join('-')) -
                new Date(a.split('-').reverse().join('-'))
            );

            // Thay tháng hiện tại thành "Kỳ này" nếu có và không lọc theo năm
            if (!filterYear) {
                const currentDate = new Date();
                const currentMonthYear = `${(currentDate.getMonth() + 1).toString().padStart(2, '0')}-${currentDate.getFullYear()}`;
                const currentIndex = sortedMonths.indexOf(currentMonthYear);

                console.log('📅 Manual day 1 cycle - current month:', currentMonthYear, 'found at index:', currentIndex);

                if (currentIndex !== -1) {
                    // Thay thế tháng hiện tại bằng "Kỳ này"
                    sortedMonths[currentIndex] = currentMonthYear; // Giữ nguyên format để logic khác hoạt động
                }
            }

            console.log('📅 Manual day 1 cycle result:', sortedMonths);
            return sortedMonths;
        } else {
            // Chu kỳ thanh toán tùy chỉnh - tạo danh sách kỳ thanh toán
            const result = this.generateBillingPeriods(billingCycle.startDay, filteredDailyData);
            console.log('📅 Custom billing cycle result:', result);
            return result;
        }
    }// Tạo danh sách các kỳ thanh toán từ dữ liệu có sẵn
    generateBillingPeriods(startDay, filteredDailyData = null) {
        // Sử dụng filteredDailyData nếu có, nếu không thì dùng this.dailyData
        const dataToUse = filteredDailyData || this.dailyData;
        
        // Đảm bảo dailyData là array
        if (!dataToUse || !Array.isArray(dataToUse) || dataToUse.length === 0) {
            console.warn('⚠️ generateBillingPeriods: No daily data available');
            return [];
        }
        
        // Lấy ngày đầu tiên và cuối cùng từ dữ liệu
        const dates = dataToUse
            .map(day => {
                if (!day.Ngày) return null;
                try {
                    return new Date(day.Ngày.split('-').reverse().join('-'));
                } catch (e) {
                    return null;
                }
            })
            .filter(Boolean)
            .sort((a, b) => a - b);

        if (dates.length === 0) return [];

        const firstDate = dates[0];
        const lastDate = dates[dates.length - 1];
        const today = new Date();

        const periods = [];

        // Bắt đầu từ ngày hiện tại và đi ngược về quá khứ
        let currentDate = new Date(today);
        let iterationCount = 0;

        while (currentDate >= firstDate && iterationCount < 24) {
            iterationCount++;

            // Tính chu kỳ thanh toán cho ngày hiện tại
            const periods_info = this.tinhngaydauky(startDay, currentDate);

            // Kiểm tra xem có phải kỳ hiện tại không (kỳ chứa ngày hôm nay)
            const isCurrentPeriod = today >= periods_info.start && today <= periods_info.end_ky;

            // Xử lý kỳ nếu:
            // 1. Có dữ liệu trong kỳ, HOẶC  
            // 2. Là kỳ hiện tại (luôn hiển thị kỳ hiện tại dù chưa có đủ dữ liệu)
            const shouldIncludePeriod = periods_info.start <= lastDate || isCurrentPeriod;

            if (shouldIncludePeriod) {                // Kiểm tra xem chu kỳ này có dữ liệu không
                const hasDataInPeriod = dataToUse.some(day => {
                    const dayDate = new Date(day.Ngày.split('-').reverse().join('-'));
                    return dayDate >= periods_info.start && dayDate <= periods_info.end_ky;
                });

                // Thêm kỳ nếu có dữ liệu HOẶC là kỳ hiện tại
                if (hasDataInPeriod || isCurrentPeriod) {                    // Logic hiển thị tháng theo chuẩn EVN:
                    // Kỳ thanh toán được đặt tên theo tháng kết thúc (tháng hóa đơn)
                    // VD: Kỳ 10/6 → 9/7 = "Kỳ tháng 7" vì hóa đơn phát hành tháng 7
                    let displayMonth, displayYear;

                    if (isCurrentPeriod && startDay === 1) {
                        // Kỳ hiện tại và bắt đầu từ ngày 1: luôn dùng tháng hiện tại
                        displayMonth = today.getMonth() + 1;
                        displayYear = today.getFullYear();
                    } else if (startDay === 1) {
                        // Chu kỳ theo tháng dương lịch (không phải kỳ hiện tại): dùng tháng bắt đầu
                        displayMonth = periods_info.start.getMonth() + 1;
                        displayYear = periods_info.start.getFullYear();
                    } else {
                        // Chu kỳ tùy chỉnh: dùng tháng kết thúc (tháng hóa đơn)
                        displayMonth = periods_info.end_ky.getMonth() + 1;
                        displayYear = periods_info.end_ky.getFullYear();
                    }

                    const periodLabel = `${displayMonth.toString().padStart(2, '0')}-${displayYear}`;

                    if (!periods.includes(periodLabel)) {
                        periods.push(periodLabel);
                    }
                }
            }

            // Lùi về tháng trước
            currentDate.setMonth(currentDate.getMonth() - 1);
        }

        // Đã sắp xếp từ mới nhất đến cũ nhất rồi
        return periods;
    }

    // Tính trend cho summary cards theo chu kỳ thanh toán
    calculateTrendData(recentMonths) {
        const billingCycle = this.getBillingCycle();

        return recentMonths.map((monthYear, index) => {
            const monthNum = monthYear.split('-')[0];
            // Lấy dữ liệu theo chu kỳ thanh toán thay vì tháng dương lịch
            let monthDataArr;
            if (billingCycle.type === 'calendar') {
                // Theo tháng dương lịch
                monthDataArr = this.dailyData.filter(d =>
                    d.Ngày.slice(3, 10) === monthYear && d["Điện tiêu thụ (kWh)"] > 0
                );
            } else if (billingCycle.type === 'cycle' && billingCycle.startDay === 1) {
                // Chu kỳ được cấu hình thủ công từ ngày 1 - xử lý như tháng dương lịch
                monthDataArr = this.dailyData.filter(d =>
                    d.Ngày.slice(3, 10) === monthYear && d["Điện tiêu thụ (kWh)"] > 0
                );
            } else {
                // Theo chu kỳ thanh toán
                monthDataArr = this.getDataByBillingPeriod(monthYear, billingCycle.startDay)
                    .filter(d => d["Điện tiêu thụ (kWh)"] > 0);
            } let min = 0, max = 0, avg = 0, minDay = '', maxDay = '';
            let trend = 'flat', trendValue = 0, trendPercent = 0, badge = '';
            let sparkline = '';
            let totalConsumption = 0, monthlyCost = 0;

            if (monthDataArr.length > 0) {
                const values = monthDataArr.map(d => d["Điện tiêu thụ (kWh)"]);
                min = Math.min(...values);
                max = Math.max(...values);
                avg = values.reduce((a, b) => a + b, 0) / values.length;
                totalConsumption = values.reduce((a, b) => a + b, 0);
                minDay = monthDataArr.find(d => d["Điện tiêu thụ (kWh)"] === min)?.Ngày || '';
                maxDay = monthDataArr.find(d => d["Điện tiêu thụ (kWh)"] === max)?.Ngày || '';

                // Tìm dữ liệu tiền điện từ monthlyData (theo cả Tháng và Năm)
                const [monthStr, yearStr] = monthYear.split('-');
                const targetYear = this.normalizeYearValue(yearStr);
                const monthlyDataItem = this.monthlyData?.TienDien?.find(item => {
                    const itemMonth = item.Tháng.toString().padStart(2, '0');
                    const itemYear = this.normalizeYearValue(item.Năm) || new Date().getFullYear();
                    const targetMonth = monthNum.toString().padStart(2, '0');
                    return itemMonth === targetMonth && itemYear === targetYear;
                });

                if (monthlyDataItem && monthlyDataItem["Tiền Điện"]) {
                    monthlyCost = parseInt(monthlyDataItem["Tiền Điện"] || 0);
                } else {
                    // Nếu không có dữ liệu từ monthlyData, tính từ sản lượng
                    if (totalConsumption > 0) {
                        const costCalculation = this.tinhTienDien(totalConsumption);
                        monthlyCost = costCalculation.total;
                    } else {
                        monthlyCost = 0;
                    }
                }                // Tính trend so với chu kỳ trước
                if (index < recentMonths.length - 1) {
                    const prevMonth = recentMonths[index + 1];
                    let prevArr;

                    if (billingCycle.type === 'calendar') {
                        prevArr = this.dailyData.filter(d =>
                            d.Ngày.slice(3, 10) === prevMonth && d["Điện tiêu thụ (kWh)"] > 0
                        );
                    } else if (billingCycle.type === 'cycle' && billingCycle.startDay === 1) {
                        // Chu kỳ được cấu hình thủ công từ ngày 1 - xử lý như tháng dương lịch
                        prevArr = this.dailyData.filter(d =>
                            d.Ngày.slice(3, 10) === prevMonth && d["Điện tiêu thụ (kWh)"] > 0
                        );
                    } else {
                        prevArr = this.getDataByBillingPeriod(prevMonth, billingCycle.startDay)
                            .filter(d => d["Điện tiêu thụ (kWh)"] > 0);
                    }

                    const prevAvg = prevArr.length > 0 ?
                        prevArr.map(d => d["Điện tiêu thụ (kWh)"]).reduce((a, b) => a + b, 0) / prevArr.length : 0;

                    trendValue = avg - prevAvg;
                    trendPercent = prevAvg > 0 ? (trendValue / prevAvg) * 100 : 0;

                    if (trendValue > 0.01) trend = 'up';
                    else if (trendValue < -0.01) trend = 'down';

                    // Badge nếu tăng/giảm mạnh
                    if (trendPercent > 20) badge = '<span class="trend-badge">Tăng mạnh</span>';
                    else if (trendPercent < -20) badge = '<span class="trend-badge">Giảm mạnh</span>';
                }

                // Tạo sparkline SVG
                const points = values.map((v, i) =>
                    `${i * (60 / (values.length - 1))},${18 - (v - min) / (max - min + 0.01) * 16}`
                ).join(' ');
                sparkline = `<svg class='sparkline'><polyline fill='none' stroke='#e961ab' stroke-width='2' points='${points}'/></svg>`;
            } return {
                monthNum,
                monthYear,
                min,
                max,
                avg,
                minDay,
                maxDay,
                trend,
                trendValue,
                trendPercent,
                badge,
                sparkline,
                dataCount: monthDataArr.length,
                isCurrentPeriod: index === 0, // Tháng đầu tiên trong danh sách là kỳ hiện tại
                totalConsumption, // Tổng sản lượng tháng
                monthlyCost // Tiền điện tháng
            };
        });
    }

    // Tính tiền điện theo bậc thang (từ NPC utils.py)
    tinhTienDien(kwh) {
        if (!kwh || kwh <= 0) {
            return { total: 0, details: {} };
        }

        const tiers = [
            { limit: 50, price: 1984 },
            { limit: 50, price: 2050 },
            { limit: 100, price: 2380 },
            { limit: 100, price: 2998 },
            { limit: 100, price: 3350 },
            { limit: Infinity, price: 3460 }
        ];

        let totalCost = 0;
        let remainingKwh = kwh;
        let tierDetails = [];

        for (let i = 0; i < tiers.length; i++) {
            const tier = tiers[i];
            const kwhInTier = Math.min(remainingKwh, tier.limit);
            const cost = kwhInTier * tier.price;

            totalCost += cost;
            tierDetails.push({
                tier: i + 1,
                price: tier.price,
                kwh: kwhInTier,
                cost: cost
            });

            remainingKwh -= kwhInTier;
            if (remainingKwh <= 0) break;
        }

        const tax = totalCost * 0.08;
        const totalWithTax = totalCost + tax;

        return {
            total: Math.round(totalWithTax),
            details: {
                subtotal: Math.round(totalCost),
                tax: Math.round(tax),
                tiers: tierDetails
            }
        };
    }    // Tính toán dữ liệu kỳ hiện tại
    calculateCurrentPeriod() {
        const billingCycle = this.getBillingCycle();
        const today = new Date();

        // Tính chu kỳ hiện tại
        const currentPeriod = this.tinhngaydauky(billingCycle.startDay, today);

        // Lấy dữ liệu trong kỳ hiện tại
        const currentPeriodData = this.dailyData.filter(day => {
            if (!day.Ngày) return false;
            const dayDate = new Date(day.Ngày.split('-').reverse().join('-'));
            return dayDate >= currentPeriod.start && dayDate <= currentPeriod.end_ky &&
                day["Điện tiêu thụ (kWh)"] > 0;
        });

        if (currentPeriodData.length === 0) {
            return null;
        }

        // Tính tổng tiêu thụ
        const totalConsumption = currentPeriodData.reduce((sum, day) =>
            sum + day["Điện tiêu thụ (kWh)"], 0
        );

        // Tính tiền điện
        const billCalculation = this.tinhTienDien(totalConsumption);

        // Xác định tháng hiển thị
        let displayMonth, displayYear;
        if (billingCycle.type === 'calendar') {
            // Chu kỳ theo tháng dương lịch - dùng tháng hiện tại
            displayMonth = today.getMonth() + 1;
            displayYear = today.getFullYear();
        } else if (billingCycle.startDay === 1 && billingCycle.type === 'cycle') {
            // Chu kỳ được cấu hình thủ công từ ngày 1 - dùng tháng hiện tại
            displayMonth = today.getMonth() + 1;
            displayYear = today.getFullYear();
        } else {
            // Chu kỳ tùy chỉnh khác - dùng tháng kết thúc kỳ
            displayMonth = currentPeriod.end_ky.getMonth() + 1;
            displayYear = currentPeriod.end_ky.getFullYear();
        }

        return {
            month: displayMonth,
            year: displayYear,
            consumption: Math.round(totalConsumption * 100) / 100, // Làm tròn 2 chữ số
            cost: billCalculation.total,
            days: currentPeriodData.length,
            isCurrentPeriod: true,
            period: {
                start: currentPeriod.start,
                end: currentPeriod.end_ky
            },
            details: billCalculation.details
        };
    }

    // Kiểm tra xem tháng có phải là kỳ hiện tại không
    isCurrentPeriodMonth(monthYear, index) {
        const billingCycle = this.getBillingCycle();
        const today = new Date();

        if (billingCycle.type === 'calendar') {
            // Tháng dương lịch: kiểm tra có phải tháng hiện tại không
            const currentMonthYear = `${(today.getMonth() + 1).toString().padStart(2, '0')}-${today.getFullYear()}`;
            return monthYear === currentMonthYear;
        } else if (billingCycle.type === 'cycle' && billingCycle.startDay === 1) {
            // Chu kỳ được cấu hình thủ công từ ngày 1: kiểm tra có phải tháng hiện tại không
            const currentMonthYear = `${(today.getMonth() + 1).toString().padStart(2, '0')}-${today.getFullYear()}`;
            return monthYear === currentMonthYear;
        } else {
            // Chu kỳ thanh toán tùy chỉnh: chỉ tháng đầu tiên là kỳ hiện tại
            return index === 0;
        }
    }

    // Lấy danh sách các năm có trong dữ liệu
    getAvailableYears() {
        const years = new Set();
        
        // Lấy từ monthly data
        if (this.monthlyData && this.monthlyData.SanLuong) {
            this.monthlyData.SanLuong.forEach(item => {
                if (item.Năm) {
                    const year = this.normalizeYearValue(item.Năm);
                    if (year !== null) {
                        years.add(year);
                    }
                }
            });
        }
        
        // Lấy từ daily data
        if (this.dailyData && Array.isArray(this.dailyData)) {
            this.dailyData.forEach(day => {
                if (day.Ngày) {
                    const year = parseInt(day.Ngày.split('-')[2]);
                    if (!isNaN(year)) {
                        years.add(year);
                    }
                }
            });
        }
        
        return Array.from(years).sort((a, b) => b - a);
    }

    // Lọc monthly data theo năm
    getFilteredMonthlyData(year) {
        return this.getMonthlyAggregation(year);
    }
}

// Export cho sử dụng global
window.DataManager = DataManager;
