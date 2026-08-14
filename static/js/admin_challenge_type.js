(function () {
    'use strict';

    function ready(fn) {
        if (document.readyState !== 'loading') fn();
        else document.addEventListener('DOMContentLoaded', fn);
    }

    ready(function () {
        var typeField = document.getElementById('id_challenge_type');
        if (!typeField) return;

        var decreasingSection = document.querySelector('.challenge-decreasing-fields');
        var dependencyInline = document.querySelector('.inline-group');

        function update() {
            var value = typeField.value;
            if (decreasingSection) {
                decreasingSection.style.display = value === 'decreasing' ? '' : 'none';
            }
            if (dependencyInline) {
                dependencyInline.style.display = value === 'dependent' ? '' : 'none';
            }
        }

        typeField.addEventListener('change', update);
        update();
    });
})();
