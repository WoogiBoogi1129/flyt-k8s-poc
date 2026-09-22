# Build context contains qemu-kvm.src.rpm from the official CentOS Stream Koji build.
# Preserve the exact launcher QEMU downstream patches and machine types.
ARG CENTOS_IMAGE=quay.io/centos/centos@sha256:40f7299e671cc57bae0cbb443cd999e700b12f7f408bac51d0430c07244dfdac
ARG LAUNCHER_IMAGE=quay.io/kubevirt/virt-launcher@sha256:2a47dceaa6ed4176175f58f4f7d927064f55d935fdd56e3999ac4dbe7d3a8e6a
FROM ${CENTOS_IMAGE} AS deps
USER 0
RUN dnf -y install dnf-plugins-core && dnf config-manager --set-enabled crb \
    && dnf -y install rpm-build redhat-rpm-config gcc gcc-c++ make ninja-build \
       glib2-devel pixman-devel zlib-devel libaio-devel liburing-devel libcap-ng-devel \
       libseccomp-devel openssl-devel libfdt-devel python3 python3-pip bzip2 tar xz \
       git diffutils patch findutils which cpio \
    && dnf clean all
FROM deps AS build
COPY qemu-kvm.src.rpm /tmp/qemu-kvm.src.rpm
RUN echo 'daf957b455f55bac4aa7268a5d57d279ae65aa579f89b0b6e758e7df9e9cafad  /tmp/qemu-kvm.src.rpm' | sha256sum -c - \
    && rpm -i /tmp/qemu-kvm.src.rpm \
    && rpmbuild -bp --nodeps /root/rpmbuild/SPECS/qemu-kvm.spec
RUN python3 -m pip install --no-cache-dir meson==1.8.1 tomli==2.2.1 pycotap==1.3.1
RUN set -eux; cd /root/rpmbuild/BUILD/qemu-10.1.0; \
    printf '\nCONFIG_IVSHMEM=y\n' >> configs/devices/x86_64-softmmu/x86_64-rh-devices.mak; \
    mkdir flyt-build; cd flyt-build; \
    ../configure --target-list=x86_64-softmmu --with-devices-x86_64=x86_64-rh-devices \
      --prefix=/opt/flyt-qemu --disable-docs --disable-werror --disable-guest-agent \
      --enable-kvm --enable-linux-aio --enable-linux-io-uring --enable-vhost-net \
      --enable-seccomp --disable-modules \
      --with-pkgversion=flyt-ivshmem-qemu-kvm-10.1.0-20.el9; \
    ninja -j16 qemu-system-x86_64; \
    install -D qemu-system-x86_64 /out/qemu-kvm; \
    mkdir -p /out/lib; \
    ldd /out/qemu-kvm | awk '/=> \// {print $3}' | xargs -r -I '{}' cp -L '{}' /out/lib/; \
    /out/qemu-kvm -device help | grep 'name "ivshmem-plain"'; \
    sha256sum /tmp/qemu-kvm.src.rpm /out/qemu-kvm > /out/build-sha256.txt
FROM ${LAUNCHER_IMAGE}
USER 0
COPY --from=build /out /opt/flyt-qemu
RUN mv /usr/libexec/qemu-kvm /usr/libexec/qemu-kvm.original \
    && printf '#!/bin/sh\nexport LD_LIBRARY_PATH=/opt/flyt-qemu/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\nexec /opt/flyt-qemu/qemu-kvm -L /opt/flyt-qemu/share/qemu "$@"\n' > /usr/libexec/qemu-kvm \
    && chmod 0755 /usr/libexec/qemu-kvm \
    && mkdir -p /opt/flyt-qemu/share/qemu \
    && cp -a /usr/share/qemu-kvm/. /opt/flyt-qemu/share/qemu/ \
    && for firmware in /usr/share/seabios/* /usr/share/seavgabios/*; do \
         if test -f "$firmware"; then ln -sf "$firmware" /opt/flyt-qemu/share/qemu/; fi; done \
    && /usr/libexec/qemu-kvm -device help | grep 'name "ivshmem-plain"'
USER 107
