package main

import (
    "context"
    "flag"
    "os"
    "strings"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    impl "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/internal/controller"
    appsv1 "k8s.io/api/apps/v1"
    corev1 "k8s.io/api/core/v1"
    netv1 "k8s.io/api/networking/v1"
    "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
    "k8s.io/apimachinery/pkg/runtime"
    "k8s.io/apimachinery/pkg/types"
    clientgoscheme "k8s.io/client-go/kubernetes/scheme"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/cache"
    "sigs.k8s.io/controller-runtime/pkg/client"
    "sigs.k8s.io/controller-runtime/pkg/handler"
    "sigs.k8s.io/controller-runtime/pkg/healthz"
    "sigs.k8s.io/controller-runtime/pkg/log/zap"
    metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"
    "sigs.k8s.io/controller-runtime/pkg/reconcile"
)
func main(){
    ns:=flag.String("namespace","flyt-hami-stage3","only namespace managed by this controller")
    flag.Parse()
    ctrl.SetLogger(zap.New())
    if !strings.HasPrefix(*ns,"flyt-hami-"){panic("dedicated flyt-hami-* namespace required")}
    scheme:=runtime.NewScheme();must(clientgoscheme.AddToScheme(scheme));must(api.AddToScheme(scheme))
    mgr,err:=ctrl.NewManager(ctrl.GetConfigOrDie(),ctrl.Options{Scheme:scheme,
        Cache:cache.Options{DefaultNamespaces:map[string]cache.Config{*ns:{}}},
        Metrics:metricsserver.Options{BindAddress:"0"},HealthProbeBindAddress:":8081",
        LeaderElection:true,LeaderElectionID:"flyt-stage3-controller",LeaderElectionNamespace:*ns})
    must(err)
    base:=impl.Base{Client:mgr.GetClient(),Reader:mgr.GetAPIReader(),Scheme:scheme,Events:mgr.GetEventRecorderFor("flyt-controller")}
    allWorkers:=handler.EnqueueRequestsFromMapFunc(func(ctx context.Context,o client.Object)[]reconcile.Request{
        list:=&api.FlytWorkerList{};if err:=base.Reader.List(ctx,list,client.InNamespace(*ns));err!=nil{return nil}
        out:=[]reconcile.Request{};for _,w:=range list.Items{out=append(out,reconcile.Request{NamespacedName:client.ObjectKeyFromObject(&w)})};return out
    })
    profileForWorker:=handler.EnqueueRequestsFromMapFunc(func(_ context.Context,o client.Object)[]reconcile.Request{
        w,ok:=o.(*api.FlytWorker);if !ok{return nil};return []reconcile.Request{{NamespacedName:types.NamespacedName{Namespace:w.Namespace,Name:w.Spec.ProfileRef.Name}}}
    })
    planeForWorker:=handler.EnqueueRequestsFromMapFunc(func(_ context.Context,o client.Object)[]reconcile.Request{
        w,ok:=o.(*api.FlytWorker);if !ok{return nil};return []reconcile.Request{{NamespacedName:types.NamespacedName{Namespace:w.Namespace,Name:w.Spec.ControlPlaneRef.Name}}}
    })
    vmiForWorker:=handler.EnqueueRequestsFromMapFunc(func(_ context.Context,o client.Object)[]reconcile.Request{
        w,ok:=o.(*api.FlytWorker);if !ok{return nil};return []reconcile.Request{{NamespacedName:types.NamespacedName{Namespace:w.Namespace,Name:w.Spec.VMIRef.Name}}}
    })
    allProfiles:=handler.EnqueueRequestsFromMapFunc(func(ctx context.Context,_ client.Object)[]reconcile.Request{
        list:=&api.FlytGPUProfileList{};if err:=base.Reader.List(ctx,list,client.InNamespace(*ns));err!=nil{return nil}
        out:=[]reconcile.Request{};for _,p:=range list.Items{out=append(out,reconcile.Request{NamespacedName:client.ObjectKeyFromObject(&p)})};return out
    })
    vmi:=&unstructured.Unstructured{};vmi.SetGroupVersionKind(impl.VMIGVK)
    vm:=&unstructured.Unstructured{};vm.SetGroupVersionKind(impl.VMGVK)
    allRequests:=handler.EnqueueRequestsFromMapFunc(func(ctx context.Context,_ client.Object)[]reconcile.Request{
        list:=&api.FlytGPURequestList{};if err:=base.Reader.List(ctx,list,client.InNamespace(*ns));err!=nil{return nil}
        out:=[]reconcile.Request{};for _,q:=range list.Items{out=append(out,reconcile.Request{NamespacedName:client.ObjectKeyFromObject(&q)})};return out
    })
    allVMIs:=handler.EnqueueRequestsFromMapFunc(func(ctx context.Context,_ client.Object)[]reconcile.Request{
        list:=&unstructured.UnstructuredList{};gvk:=impl.VMIGVK;gvk.Kind+="List";list.SetGroupVersionKind(gvk)
        if err:=base.Reader.List(ctx,list,client.InNamespace(*ns));err!=nil{return nil}
        out:=[]reconcile.Request{};for _,v:=range list.Items{out=append(out,reconcile.Request{NamespacedName:client.ObjectKeyFromObject(&v)})};return out
    })
    must(ctrl.NewControllerManagedBy(mgr).Named("flyt-vmi").For(vmi).
        Watches(&api.FlytGPURequest{},allVMIs).Watches(vm.DeepCopy(),allVMIs).
        Watches(&api.FlytWorker{},vmiForWorker).Complete(&impl.VMIReconciler{Base:base}))
    must(ctrl.NewControllerManagedBy(mgr).Named("flyt-request").For(&api.FlytGPURequest{}).
        Watches(&api.FlytWorker{},allRequests).Watches(&api.FlytGPUProfile{},allRequests).
        Watches(&api.FlytControlPlane{},allRequests).Watches(vm.DeepCopy(),allRequests).Watches(vmi.DeepCopy(),allRequests).
        Complete(&impl.RequestReconciler{Base:base}))
    must(ctrl.NewControllerManagedBy(mgr).Named("flyt-profile").For(&api.FlytGPUProfile{}).
        Watches(&api.FlytWorker{},profileForWorker).Watches(&corev1.Node{},allProfiles).
        Complete(&impl.ProfileReconciler{Base:base}))
    must(ctrl.NewControllerManagedBy(mgr).Named("flyt-control-plane").For(&api.FlytControlPlane{}).
        Owns(&appsv1.Deployment{}).Owns(&corev1.Service{}).Owns(&corev1.ConfigMap{}).Owns(&netv1.NetworkPolicy{}).
        Watches(&api.FlytWorker{},planeForWorker).Complete(&impl.PlaneReconciler{Base:base}))
    must(ctrl.NewControllerManagedBy(mgr).Named("flyt-worker").For(&api.FlytWorker{}).
        Owns(&appsv1.Deployment{}).Owns(&corev1.Service{}).Owns(&corev1.ConfigMap{}).Owns(&netv1.NetworkPolicy{}).
        Watches(&corev1.Pod{},allWorkers).Watches(&api.FlytGPUProfile{},allWorkers).
        Watches(&api.FlytGPURequest{},allWorkers).Watches(vm.DeepCopy(),allWorkers).
        Watches(&api.FlytControlPlane{},allWorkers).Watches(vmi.DeepCopy(),allWorkers).
        Complete(&impl.WorkerReconciler{Base:base}))
    must(mgr.AddHealthzCheck("healthz",healthz.Ping));must(mgr.AddReadyzCheck("readyz",healthz.Ping))
    if err=mgr.Start(ctrl.SetupSignalHandler());err!=nil{ctrl.Log.Error(err,"manager stopped");os.Exit(1)}
}
func must(err error){if err!=nil{panic(err)}}
